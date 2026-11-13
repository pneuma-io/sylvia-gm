"""Structured Campaign Plans for No Ruleset campaigns.

A No Ruleset Plan is an ordinary format-2 Campaign Plan whose Ruleset and Campaign Setting
bindings are both null and whose closure hash is the admitted empty closure. With no
Setting to reference, the Plan's own ``initial_state.entities`` are its world: each
person, place or item is addressed as ``entities/<id>`` wherever a Ruleset Plan would use
a Setting path (starting location, Setting reference groups, act references). Each entity
``state`` carries a ``name``, optional ``visibility``/``known_by``, and free-form story
attributes such as an item's description, purpose and location. Accepting the Plan seeds
those entities into Campaign State, so the GM starts from them and keeps them current.
A No Ruleset Plan cannot carry Ruleset state, party placements or Setting characters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sylvia_gm.domain.authority import validate_authority_identity

NO_RULESET_ENTITY_PREFIX = "entities/"
_ENTITY_CONTROL_FIELDS = frozenset({"name", "visibility", "known_by"})


def is_no_ruleset_plan(plan: Mapping[str, object]) -> bool:
    """Return whether *plan* declares the No Ruleset binding (both bindings null)."""

    bindings = plan.get("bindings")
    return (
        isinstance(bindings, Mapping)
        and "ruleset" in bindings
        and "campaign_setting" in bindings
        and bindings["ruleset"] is None
        and bindings["campaign_setting"] is None
    )


def _entities(plan: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    initial_state = plan.get("initial_state")
    entities = initial_state.get("entities") if isinstance(initial_state, Mapping) else None
    if not isinstance(entities, Sequence) or isinstance(entities, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in entities if isinstance(item, Mapping))


def validate_no_ruleset_world(plan: Mapping[str, object]) -> frozenset[str]:
    """Check a No Ruleset Plan's own world and return the paths it may reference.

    Raises ``ValueError`` with a plain reason when the Plan uses Ruleset-only content or
    an entity is not a self-addressed, named world record.
    """

    initial_state = plan.get("initial_state")
    if isinstance(initial_state, Mapping):
        if initial_state.get("mechanics"):
            raise ValueError("A No Ruleset Plan cannot declare Ruleset mechanical state")
        if "party_placements" in initial_state:
            raise ValueError("A No Ruleset Plan cannot declare Ruleset party placements")
    party = plan.get("starting_party")
    if isinstance(party, Mapping):
        player = party.get("player_character")
        if isinstance(player, Mapping) and player.get("mode") == "fixed":
            raise ValueError("A No Ruleset Plan cannot fix a Setting player character")
        if party.get("required_companions"):
            raise ValueError("A No Ruleset Plan cannot require Setting companions")
    paths: set[str] = set()
    for entity in _entities(plan):
        identity = validate_authority_identity(entity.get("id"), "No Ruleset Plan entity")
        path = NO_RULESET_ENTITY_PREFIX + identity
        source = entity.get("source_ref")
        if not isinstance(source, Mapping) or dict(source) != {"id": identity, "path": path}:
            raise ValueError(
                f"No Ruleset Plan entity {identity} must use source_ref "
                f'{{"id": "{identity}", "path": "{path}"}}'
            )
        state = entity.get("state")
        if not isinstance(state, Mapping):
            raise ValueError(f"No Ruleset Plan entity {identity} state is invalid")
        name = state.get("name")
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 300:
            raise ValueError(f"No Ruleset Plan entity {identity} needs a name")
        if state.get("visibility", "gm_private") not in {"player_known", "gm_private"}:
            raise ValueError(f"No Ruleset Plan entity {identity} visibility is invalid")
        known_by = state.get("known_by", [])
        if not isinstance(known_by, Sequence) or isinstance(known_by, (str, bytes, bytearray)):
            raise ValueError(f"No Ruleset Plan entity {identity} knowledge is invalid")
        for item in known_by:
            validate_authority_identity(item, "No Ruleset Plan entity knowledge")
        paths.add(path)
    return frozenset(paths)


def no_ruleset_world_records(
    plan: Mapping[str, object],
) -> tuple[tuple[str, dict[str, object]], ...]:
    """Return ``(record_id, entity payload)`` pairs that seed Campaign State at acceptance.

    Prepared entities stay ``gm_private`` unless the Plan marks them ``player_known``.
    """

    records: list[tuple[str, dict[str, object]]] = []
    for entity in _entities(plan):
        identity = str(entity["id"])
        state = entity["state"]
        assert isinstance(state, Mapping)
        known_by = state.get("known_by", [])
        records.append((
            identity,
            {
                "entity_kind": str(entity["kind"]),
                "name": str(state["name"]),
                "attributes": {
                    str(key): value
                    for key, value in state.items()
                    if key not in _ENTITY_CONTROL_FIELDS
                },
                "visibility": str(state.get("visibility", "gm_private")),
                "known_by": [str(item) for item in known_by]
                if isinstance(known_by, Sequence) else [],
                "source_ref": identity,
                "origin": "accepted_initial_state",
            },
        ))
    return tuple(records)
