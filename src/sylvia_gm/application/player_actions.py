"""Application-owned origins for mechanics invoked through player controls."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from sylvia_gm.domain.authority import (
    CampaignSnapshot,
    MechanicOutcome,
    SafePayload,
    owned_payload,
    validate_authority_identity,
)
from sylvia_gm.domain.errors import CommandRejected

PLAYER_ACTION_ORIGIN = "sylvia_player_action"
PlayerActionChecker = Callable[[SafePayload, SafePayload, SafePayload], str | None]
PlayerActionPreviewer = Callable[[SafePayload, SafePayload, SafePayload], SafePayload | None]
_LOCAL_NAME = re.compile(r"^_[A-Za-z][A-Za-z0-9_]{0,63}$")


def _valid_control_queries(queries: object, calculations: object) -> bool:
    """Admit bounded physical queries and scalar calculations, with no effect dispatch."""

    if (not isinstance(queries, (list, tuple)) or len(queries) > 8
            or not isinstance(calculations, (list, tuple)) or len(calculations) > 24):
        return False
    names: set[str] = set()
    for item in queries:
        if not isinstance(item, Mapping) or set(item) != {"spatial_query", "store_as"}:
            return False
        name, body = item["store_as"], item["spatial_query"]
        if (not isinstance(name, str) or not _LOCAL_NAME.fullmatch(name) or name in names
                or not isinstance(body, Mapping) or set(body) != {"scene", "operation", "query"}
                or not isinstance(body["scene"], str) or not body["scene"].startswith("state.")
                or not isinstance(body["operation"], str)
                or body["operation"] not in {"path", "ray", "exposure", "area", "crossed_zones"}
                or not isinstance(body["query"], Mapping) or "kind" in body["query"]):
            return False
        names.add(name)
    for item in calculations:
        if (not isinstance(item, Mapping) or "set" not in item
                or set(item) - {"set", "condition"}):
            return False
        body = item["set"]
        if not isinstance(body, Mapping) or set(body) != {"field", "value"}:
            return False
        name, expression = body["field"], body["value"]
        condition = item.get("condition", "true")
        if (not isinstance(name, str) or not _LOCAL_NAME.fullmatch(name) or name in names
                or not isinstance(expression, str) or not 0 < len(expression) <= 500
                or not isinstance(condition, str) or not 0 < len(condition) <= 500):
            return False
        names.add(name)
    return True


def player_control_origin(
    source: SafePayload, *, expected_revision: int, selection: SafePayload | None,
    session_id: str | None,
) -> SafePayload:
    """Identify a declared UI control; the caller cannot supply replacement tool arguments."""

    if not isinstance(source, Mapping):
        raise CommandRejected("Player control source is invalid")
    origin = {
        "expected_revision": expected_revision, "selection": selection or {},
        "session_id": session_id,
    }
    if source.get("kind") == "panel" and set(source) == {"kind", "surface_id", "action_id"}:
        validate_authority_identity(source["surface_id"], "UI surface identity")
        validate_authority_identity(source["action_id"], "UI surface action identity")
        return owned_payload({
            **origin, "control": "panel", "surface_id": source["surface_id"],
            "panel_action_id": source["action_id"],
        })
    if source.get("kind") == "result_card" and set(source) == {"kind", "action_id", "button_index"}:
        validate_authority_identity(source["action_id"], "Saved card action identity")
        index = source["button_index"]
        if type(index) is not int or not 0 <= index < 8:
            raise CommandRejected("Saved card button index is invalid")
        return owned_payload({
            **origin, "control": "result_card", "source_action_id": source["action_id"],
            "button_index": index,
        })
    if source.get("kind") == "mechanics_choice" and set(source) == {
        "kind", "action_id", "choice_id", "choice_sequence", "option_id", "resolution_id",
    }:
        for key in ("action_id", "choice_id", "option_id", "resolution_id"):
            validate_authority_identity(source[key], "Mechanics choice " + key)
        sequence = source["choice_sequence"]
        if type(sequence) is not int or not 0 <= sequence < 64:
            raise CommandRejected("Mechanics choice sequence is invalid")
        return owned_payload({**origin, "control": "mechanics_choice",
            "source_action_id": source["action_id"], "choice_id": source["choice_id"],
            "choice_sequence": sequence, "option_id": source["option_id"],
            "resolution_id": source["resolution_id"]})
    raise CommandRejected("Player control source is invalid")


@dataclass(frozen=True)
class PlayerControls:
    """Ruleset policy for confirming one direct action and requesting its GM response."""

    confirmation: str = "none"
    response: str = "on_request"
    preview: SafePayload | None = None
    actor_parameter: str | None = None
    checks: tuple[SafePayload, ...] = ()
    queries: tuple[SafePayload, ...] = ()
    calculations: tuple[SafePayload, ...] = ()
    map_preview: SafePayload | None = None
    available: bool = True


def parse_player_controls(value: object) -> PlayerControls:
    """Disable malformed direct controls without withdrawing the underlying chat mechanic."""

    if value is None:
        return PlayerControls()
    if not isinstance(value, Mapping) or set(value) - {
        "confirmation", "response", "preview", "actor_parameter", "checks", "queries",
        "calculations", "map_preview",
    }:
        return PlayerControls(available=False)
    confirmation = value.get("confirmation", "none")
    response = value.get("response", "on_request")
    preview = value.get("preview")
    actor_parameter = value.get("actor_parameter")
    checks = value.get("checks", ())
    queries, calculations = value.get("queries", ()), value.get("calculations", ())
    if not _valid_control_queries(queries, calculations):
        return PlayerControls(available=False)
    map_preview = value.get("map_preview")
    if map_preview is not None:
        query_kinds = {item["store_as"]: item["spatial_query"]["operation"] for item in queries}
        if (not isinstance(map_preview, Mapping) or not map_preview
                or set(map_preview) - {"path", "ray", "exposure", "crossed_zones"}
                or any(not isinstance(name, str) or query_kinds.get(name) != kind
                       for kind, name in map_preview.items())):
            return PlayerControls(available=False)
    if (not isinstance(checks, (list, tuple)) or len(checks) > 32 or any(
        not isinstance(check, Mapping) or set(check) != {"condition", "reason"}
        or not isinstance(check["condition"], str) or not check["condition"].strip()
        or len(check["condition"]) > 4096
        or not isinstance(check["reason"], str) or not check["reason"].strip()
        or len(check["reason"]) > 256
        for check in checks
    )):
        return PlayerControls(available=False)
    if (
        confirmation not in ("none", "required")
        or response not in ("on_request", "automatic")
        or (preview is not None and not isinstance(preview, Mapping))
        or (actor_parameter is not None and (
            not isinstance(actor_parameter, str) or not actor_parameter
            or len(actor_parameter) > 128
        ))
    ):
        return PlayerControls(available=False)
    try:
        return PlayerControls(
            confirmation=confirmation, response=response,
            preview=None if preview is None else owned_payload(preview),
            actor_parameter=actor_parameter,
            checks=tuple(owned_payload(check) for check in checks),
            queries=tuple(owned_payload(query) for query in queries),
            calculations=tuple(owned_payload(calculation) for calculation in calculations),
            map_preview=None if map_preview is None else owned_payload(map_preview),
        )
    except (TypeError, ValueError):
        return PlayerControls(available=False)


def player_action_request_matches(stored: SafePayload | None, request: SafePayload) -> bool:
    """Compare the original request while preserving policy captured only on first execution."""

    return stored is not None and all(stored.get(key) == value for key, value in request.items())


def validate_player_control(
    scope: SafePayload | None, parameters: SafePayload, actor_parameter: str | None,
) -> None:
    """Keep direct actor selection inside the Ruleset's current player decision scope."""

    if scope is None:
        return
    if scope.get("available") is not True:
        raise CommandRejected("The Ruleset currently requires a GM decision")
    if actor_parameter is not None:
        actor_id = parameters.get(actor_parameter)
        allowed = {actor["id"] for actor in scope["actors"]}
        if not isinstance(actor_id, str) or actor_id not in allowed:
            raise CommandRejected("This actor is not currently controlled by the player")


def resolve_party_action_parameters(
    action: SafePayload, selection: SafePayload | None, data: object,
    *, allow_entity_ids: bool = False,
) -> SafePayload:
    """Bind the chosen visible party member to the Ruleset's declared actor parameter."""

    bindings, fixed = action.get("bindings"), action.get("params")
    participants = data.get("participants") if isinstance(data, Mapping) else None
    if (not isinstance(bindings, Mapping) or not isinstance(fixed, Mapping)
            or set(bindings.values()) != {"actor"} or len(bindings) != 1
            or not isinstance(selection, Mapping) or set(selection) != {"actor"}
            or not isinstance(participants, Sequence)):
        raise CommandRejected("Party action selection is invalid")
    actor_id = selection["actor"]
    visible_ids = {item.get("id") for item in participants if isinstance(item, Mapping)}
    entities = data.get("entities") if isinstance(data, Mapping) else None
    if allow_entity_ids and isinstance(entities, Mapping):
        visible_ids.update(entities)
    if not isinstance(actor_id, str) or actor_id not in visible_ids:
        raise CommandRejected("The selected party member is unavailable")
    return owned_payload({**fixed, **dict.fromkeys(bindings, actor_id)})


def retain_player_action_origin(
    outcome: MechanicOutcome, origin: SafePayload | None
) -> MechanicOutcome:
    """Save UI provenance in the same transaction as the real mechanics result."""

    if origin is None:
        return outcome
    return replace(
        outcome,
        consequences=outcome.consequences
        + (owned_payload({"kind": PLAYER_ACTION_ORIGIN, "origin": origin}),),
    )


def valid_player_action_consequence(payload: SafePayload, *, expected_revision: int) -> bool:
    """Recognize the owner marker that may survive a failed, fully rolled-back mechanic."""

    origin = payload.get("origin")
    if (set(payload) != {"kind", "origin"} or payload.get("kind") != PLAYER_ACTION_ORIGIN
            or not isinstance(origin, Mapping)):
        return False
    revision, selection, policy = (
        origin.get("expected_revision"), origin.get("selection"), origin.get("policy"),
    )
    if (type(revision) is not int or revision != expected_revision or revision < 0
            or not isinstance(selection, Mapping) or not isinstance(policy, Mapping)
            or set(policy) != {"confirmation", "response"}
            or policy["confirmation"] not in ("none", "required")
            or policy["response"] not in ("on_request", "automatic")):
        return False
    session_id = origin.get("session_id")
    source = (
        {"kind": "mechanics_choice", "action_id": origin.get("source_action_id"),
         "choice_id": origin.get("choice_id"), "choice_sequence": origin.get("choice_sequence"),
         "option_id": origin.get("option_id"), "resolution_id": origin.get("resolution_id")}
        if origin.get("control") == "mechanics_choice" else
        {"kind": "panel", "surface_id": origin.get("surface_id"),
         "action_id": origin.get("panel_action_id")}
        if origin.get("control") == "panel" else
        {"kind": "result_card", "action_id": origin.get("source_action_id"),
         "button_index": origin.get("button_index")}
    )
    try:
        if session_id is not None:
            validate_authority_identity(session_id, "Player control session identity")
        expected = player_control_origin(
            source, expected_revision=revision, selection=selection, session_id=session_id,
        )
        return origin == {**expected, "policy": policy}
    except (CommandRejected, TypeError, ValueError):
        return False


def player_action_origins(snapshot: CampaignSnapshot) -> dict[str, SafePayload]:
    """Read application markers without interpreting Ruleset results or live state."""

    return {
        record.action_id: owned_payload(record.payload["origin"])
        for record in snapshot.consequences
        if record.payload.get("kind") == PLAYER_ACTION_ORIGIN
        and isinstance(record.payload.get("origin"), Mapping)
    }


def player_action_records(snapshot: CampaignSnapshot) -> tuple[SafePayload, ...]:
    """Project the saved outcome, never re-executing or inferring its gameplay meaning."""

    origins = player_action_origins(snapshot)
    return tuple(
        owned_payload(
            {
                "action_id": action.action_id,
                "turn_id": action.turn_id,
                "revision": action.resulting_revision,
                "status": action.status.value,
                "origin": origins[action.action_id],
                "package_id": action.package_id,
                "tool_name": action.tool_name,
                "parameters": action.parameters,
                "result": {
                    key: value
                    for key, value in action.result.items()
                    if key != "spatial_evidence"
                },
            }
        )
        for action in snapshot.actions
        if action.action_id in origins
    )


def resolve_inventory_action_parameters(action: SafePayload, selection: SafePayload | None, data: object) -> SafePayload:
    """Bind an owned visible actor and bounded form data to a declared inventory operation."""
    bindings,fixed=action.get("bindings"),action.get("params")
    participants=data.get("participants") if isinstance(data,Mapping) else None
    if (not isinstance(bindings,Mapping) or not isinstance(fixed,Mapping)
            or not isinstance(selection,Mapping) or not isinstance(participants,Sequence)
            or not set(bindings.values())<= {"actor","declared_action"}
            or set(selection)!=set(bindings.values()) or "actor" not in selection):
        raise CommandRejected("Inventory action selection is invalid")
    actor=selection["actor"]
    if not isinstance(actor,str) or actor not in {p.get("id") for p in participants if isinstance(p,Mapping)}:
        raise CommandRejected("The selected inventory owner is unavailable")
    return owned_payload({**fixed,**{parameter:selection[source] for parameter,source in bindings.items()}})
