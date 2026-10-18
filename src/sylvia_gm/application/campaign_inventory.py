"""Prepare fallback inventory inside the existing atomic Campaign File write."""

from collections.abc import Mapping, Sequence
from typing import cast

from sylvia_gm.domain.authority import CampaignSnapshot, CampaignState, SafePayload, owned_payload
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict
from sylvia_gm.domain.inventory import (
    INVENTORY_ENTITY_KIND,
    INVENTORY_RECORD_ID,
    player_inventory,
    transfer_holding,
    validate_holdings,
)


def generic_inventory_surface(
    snapshot: CampaignState,
    entities: Sequence[Mapping[str, object]],
    *,
    binding: Mapping[str, object] | None = None,
    state_target: Mapping[str, object] | None = None,
) -> SafePayload:
    """Build one player-safe inventory surface over the selected authoritative representation."""
    visible = {
        str(item["record_id"]): str(item["name"])
        for item in entities
        if item.get("visibility") == "player_known"
        and item.get("record_id") != INVENTORY_RECORD_ID
    }
    characters = {
        participant.participant_id: str(participant.data.get("name", participant.participant_id))
        for participant in snapshot.participants
        if participant.kind == "player_character"
    }
    if binding is None:
        characters.update(
            {
                str(item["record_id"]): visible[str(item["record_id"])]
                for item in entities
                if item.get("entity_kind") in {
                    "person", "character", "player_character", "companion", "npc",
                }
                and str(item.get("record_id")) in visible
            }
        )
    visible.update(characters)
    holdings = _ledger_holdings(entities)
    projections = []
    for identity, name in characters.items():
        if binding is None:
            inventory = player_inventory(holdings, visible_entities=visible, character_id=identity)
        else:
            assert state_target is not None
            state = snapshot.state.get(f"{state_target['namespace']}.{state_target['key']}", {})
            if not isinstance(state, Mapping) or not isinstance(state.get(identity, {}), Mapping):
                raise CommandRejected("Ruleset inventory state is malformed")
            character_state = cast(Mapping[str, object], state.get(identity, {}))
            values = character_state.get(str(binding["field"]), ())
            if binding["format"] == "holdings":
                inventory = player_inventory(
                    _holding_records(values), visible_entities=visible, character_id=identity
                )
            else:
                if (
                    not isinstance(values, Sequence)
                    or isinstance(values, (str, bytes))
                    or any(not isinstance(value, str) for value in values)
                ):
                    raise CommandRejected("Ruleset inventory list is malformed")
                inventory = {
                    "items": [
                        {
                            "item_id": value,
                            "name": value,
                            "quantity": 1,
                            "kind": "item",
                            "holder": name,
                            "owner": "Unspecified",
                            "carried": True,
                            "owned": False,
                        }
                        for value in values
                    ]
                }
        projections.append({"id": identity, "name": name, "items": inventory["items"]})
    return owned_payload(
        {
            "id": "sylvia-inventory",
            "title": "Inventory",
            "icon": "▣",
            "renderer": "generic_inventory",
            "group": None,
            "order": 30,
            "default_size": 220,
            "minimum_size": 120,
            "default_placement": "docked",
            "allowed_placements": ["docked", "floating", "window"],
            "actions": [],
            "data": {"characters": projections},
        }
    )


def _holding_records(value: object) -> tuple[Mapping[str, object], ...]:
    """Narrow stored/schema-owned JSON records without permitting scalar inventory values."""
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise CommandRejected("Inventory holdings are malformed")
    return tuple(cast(Sequence[Mapping[str, object]], value))


def _ledger_holdings(entities: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    """Read the reserved ledger only; ordinary narrative inventory prose is never migrated."""
    ledger = next(
        (
            item
            for item in entities
            if item.get("record_id") == INVENTORY_RECORD_ID
            and item.get("entity_kind") == INVENTORY_ENTITY_KIND
        ),
        None,
    )
    if ledger is None:
        return ()
    attributes = ledger.get("attributes")
    if not isinstance(attributes, Mapping):
        raise CommandRejected("Inventory ledger is malformed")
    return _holding_records(attributes.get("holdings"))


def inventory_holdings(snapshot: CampaignSnapshot) -> Sequence[Mapping[str, object]]:
    """Read the latest committed fallback ledger, without interpreting narrative attributes."""
    return _ledger_holdings(snapshot.continuity.facts)


def prepare_inventory_payload(
    snapshot: CampaignSnapshot,
    updates: Sequence[Mapping[str, object]],
    *,
    operation_id: str,
    request_hash: str,
    pending_entities: Sequence[Mapping[str, object]],
) -> tuple[int, SafePayload]:
    """Replay the original prepared result or validate a new revision-fenced holding update."""
    if len(updates) != 1:
        raise CommandRejected("Supply exactly one inventory update batch")
    update = updates[0]
    expected_revision = update.get("expected_revision")
    if (
        set(update) - {"operation", "expected_revision", "holdings", "transfers"}
        or type(expected_revision) is not int
        or expected_revision < 0
    ):
        raise CommandRejected("Inventory update fields or revision are invalid")
    previous = next(
        (record for record in snapshot.continuity.records if record.operation_id == operation_id),
        None,
    )
    if previous is not None:
        if previous.payload.get("request_hash") != request_hash:
            raise IdempotencyConflict("Inventory update identity was reused with different content")
        return expected_revision, previous.payload
    entity_ids = frozenset(
        {
            *(participant.participant_id for participant in snapshot.participants),
            *(
                str(item["record_id"])
                for item in snapshot.continuity.facts
                if "entity_kind" in item and item["record_id"] != INVENTORY_RECORD_ID
            ),
            *(str(item.get("entity_id")) for item in pending_entities),
        }
    )
    try:
        holdings = {str(item["holding_id"]): item for item in inventory_holdings(snapshot)}
        replacements = _holding_records(update.get("holdings", ()))
        transfers = _holding_records(update.get("transfers", ()))
        for collection in (replacements, transfers):
            if (
                not isinstance(collection, Sequence)
                or isinstance(collection, (str, bytes))
                or len(collection) > 64
                or any(not isinstance(item, Mapping) for item in collection)
            ):
                raise ValueError("Inventory updates must be bounded lists")
        if not replacements and not transfers:
            raise ValueError("Inventory update is empty")
        replaced: set[str] = set()
        for replacement in replacements:
            identity = replacement.get("holding_id")
            if not isinstance(identity, str) or identity in replaced:
                raise ValueError("Inventory holding update identity is invalid or repeated")
            replaced.add(identity)
            old = holdings.get(identity)
            if old is not None and any(
                old[field] != replacement.get(field) for field in ("item_id", "kind", "stackable")
            ):
                raise ValueError("An existing holding cannot become a different item or currency")
            holdings[identity] = replacement
        current = validate_holdings(tuple(holdings.values()), entity_ids=entity_ids)
        for transfer in transfers:
            required = {"source_id", "destination_id", "quantity", "holder_id"}
            if set(transfer) not in (required, required | {"owner_id"}):
                raise ValueError("Inventory transfer fields are invalid")
            current = transfer_holding(
                current,
                entity_ids=entity_ids,
                source_id=cast(str, transfer["source_id"]),
                destination_id=cast(str, transfer["destination_id"]),
                quantity=cast(int, transfer["quantity"]),
                holder_id=cast(str, transfer["holder_id"]),
                owner_id=cast(str | None, transfer.get("owner_id")),
            )
    except (ValueError, TypeError, KeyError) as error:
        raise CommandRejected(str(error), code="inventory_update_invalid") from error
    return expected_revision, owned_payload(
        {
            "entity_kind": INVENTORY_ENTITY_KIND,
            "name": "Fallback inventory",
            "attributes": {"holdings": list(current)},
            "visibility": "gm_private",
            "known_by": [],
            "source_ref": None,
            "origin": "gm_commit",
            "request_hash": request_hash,
        }
    )
