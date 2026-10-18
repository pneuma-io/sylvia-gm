"""Rules-neutral holding validation and transfers; persistence stays with campaign authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from sylvia_gm.domain.authority import SafePayload, owned_payload, validate_authority_identity

INVENTORY_RECORD_ID = "sylvia-fallback-inventory"
INVENTORY_ENTITY_KIND = "inventory_ledger"
_FIELDS = frozenset(
    {
        "holding_id",
        "item_id",
        "name",
        "kind",
        "stackable",
        "quantity",
        "owner_id",
        "holder_id",
        "visibility",
        "known_by",
    }
)


def inventory_update_schema() -> dict[str, object]:
    """Expose full holdings for establishment/correction and explicit conserved transfers."""
    identity = {"type": "string", "minLength": 1}
    holding = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_FIELDS),
        "properties": {
            **dict.fromkeys(("holding_id", "item_id", "owner_id", "holder_id"), identity),
            "name": {"type": "string", "minLength": 1, "maxLength": 300},
            "kind": {"type": "string", "enum": ["item", "currency"]},
            "stackable": {"type": "boolean"},
            "quantity": {"type": "integer", "minimum": 0, "maximum": 2**53 - 1},
            "visibility": {
                "type": "string", "enum": ["player_known", "gm_private"],
                "description": (
                    "Release boundary for the entire holding. Use player_known only when all "
                    "included details are established for the player; otherwise use gm_private."
                ),
            },
            "known_by": {
                "type": "array", "items": identity, "uniqueItems": True,
                "description": (
                    "Exact character identities that know this holding in the fiction. This "
                    "does not override visibility or reveal private information to the player."
                ),
            },
        },
    }
    transfer = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_id", "destination_id", "quantity", "holder_id"],
        "properties": {
            **dict.fromkeys(("source_id", "destination_id", "holder_id", "owner_id"), identity),
            "quantity": {"type": "integer", "minimum": 1, "maximum": 2**53 - 1},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["expected_revision"],
        "properties": {
            "expected_revision": {"type": "integer", "minimum": 0},
            "holdings": {
                "type": "array",
                "items": holding,
                "maxItems": 64,
                "description": (
                    "Establish or correct holdings by stable holding_id; other holdings remain "
                    "unchanged. Zero quantity retains identity without a visible item. Quantities "
                    "are whole units of the named currency; no exchange rate is inferred."
                ),
            },
            "transfers": {
                "type": "array",
                "items": transfer,
                "maxItems": 64,
                "description": (
                    "Move existing quantities after holding updates. Omit owner_id for "
                    "loans/storage/returns; supply it only for an explicit ownership change. "
                    "Partial moves need a different destination_id."
                ),
            },
        },
    }


def inventory_holding_schema() -> dict[str, object]:
    """Return the shared structured holding shape for Ruleset projection declarations."""
    properties = cast(dict[str, object], inventory_update_schema()["properties"])
    return cast(dict[str, object], cast(dict[str, object], properties["holdings"])["items"])


def validate_holdings(
    holdings: Sequence[Mapping[str, object]],
    *,
    entity_ids: frozenset[str],
) -> tuple[SafePayload, ...]:
    """Validate one complete inventory without inferring ownership or merging distinct items."""
    result: list[SafePayload] = []
    identities: set[str] = set()
    item_shapes: dict[str, tuple[object, ...]] = {}
    item_totals: dict[str, int] = {}
    stacks: set[tuple[object, ...]] = set()
    for holding in holdings:
        if not isinstance(holding, Mapping) or set(holding) != _FIELDS:
            raise ValueError("Inventory holding fields are invalid")
        for field in ("holding_id", "item_id", "owner_id", "holder_id"):
            validate_authority_identity(holding[field], f"Inventory {field}")
        identity = str(holding["holding_id"])
        if identity in identities:
            raise ValueError("Inventory holding identity is duplicated")
        identities.add(identity)
        if any(holding[field] not in entity_ids for field in ("owner_id", "holder_id")):
            raise ValueError("Inventory owner and holder must reference known campaign entities")
        name = holding["name"]
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 300:
            raise ValueError("Inventory item name is invalid")
        quantity = holding["quantity"]
        if type(quantity) is not int or not 0 <= quantity <= 2**53 - 1:
            raise ValueError("Inventory quantity must be a nonnegative safe whole number")
        if holding["kind"] not in ("item", "currency") or type(holding["stackable"]) is not bool:
            raise ValueError("Inventory kind or stacking declaration is invalid")
        if holding["kind"] == "currency" and not holding["stackable"]:
            raise ValueError("Named currency must be stackable")
        if holding["visibility"] not in ("player_known", "gm_private"):
            raise ValueError("Inventory visibility is invalid")
        known_by = holding["known_by"]
        if not isinstance(known_by, Sequence) or isinstance(known_by, (str, bytes)):
            raise ValueError("Inventory knowledge must be an identity list")
        for identity in known_by:
            validate_authority_identity(identity, "Inventory knowledge identity")
        if len(set(known_by)) != len(known_by):
            raise ValueError("Inventory knowledge identities are duplicated")
        item_id = str(holding["item_id"])
        shape = (name, holding["kind"], holding["stackable"])
        if item_shapes.setdefault(item_id, shape) != shape:
            raise ValueError("One item identity cannot describe different items or currencies")
        item_totals[item_id] = item_totals.get(item_id, 0) + quantity
        if not holding["stackable"] and item_totals[item_id] > 1:
            raise ValueError("A distinct object cannot exist in multiple holdings")
        if quantity:
            stack = (item_id, holding["owner_id"], holding["holder_id"])
            if stack in stacks:
                raise ValueError("Identical items with the same owner and holder must stack")
            stacks.add(stack)
        result.append(owned_payload(holding))
    return tuple(result)


def transfer_holding(
    holdings: Sequence[Mapping[str, object]],
    *,
    source_id: str,
    destination_id: str,
    quantity: int,
    holder_id: str,
    entity_ids: frozenset[str],
    owner_id: str | None = None,
) -> tuple[SafePayload, ...]:
    """Move an exact quantity; loans/storage preserve ownership unless explicitly changed."""
    current = validate_holdings(holdings, entity_ids=entity_ids)
    for value in (source_id, destination_id, holder_id):
        validate_authority_identity(value, "Inventory transfer identity")
    if owner_id is not None:
        validate_authority_identity(owner_id, "Inventory transfer owner")
    if type(quantity) is not int or quantity <= 0:
        raise ValueError("Inventory transfer quantity must be a positive whole number")
    by_id = {str(item["holding_id"]): dict(item) for item in current}
    source = by_id.get(source_id)
    if source is None or quantity > cast(int, source["quantity"]):
        raise ValueError("Inventory transfer exceeds the available source quantity")
    destination_owner = source["owner_id"] if owner_id is None else owner_id
    if source_id == destination_id:
        if quantity != source["quantity"]:
            raise ValueError("A partial transfer needs a different destination holding identity")
        source.update(holder_id=holder_id, owner_id=destination_owner)
    else:
        destination = by_id.get(destination_id)
        if destination is None:
            destination = {
                **source,
                "holding_id": destination_id,
                "quantity": 0,
                "holder_id": holder_id,
                "owner_id": destination_owner,
            }
            by_id[destination_id] = destination
        if any(
            destination[field] != source[field]
            for field in ("item_id", "name", "kind", "stackable")
        ):
            raise ValueError("Inventory transfer cannot convert items or currencies")
        if destination["owner_id"] != destination_owner or destination["holder_id"] != holder_id:
            raise ValueError("Inventory destination has a different owner or holder")
        if any(destination[field] != source[field] for field in ("visibility", "known_by")):
            raise ValueError("Inventory transfer cannot merge different knowledge boundaries")
        source["quantity"] = cast(int, source["quantity"]) - quantity
        destination["quantity"] = cast(int, destination["quantity"]) + quantity
    return validate_holdings(tuple(by_id.values()), entity_ids=entity_ids)


def player_inventory(
    holdings: Sequence[Mapping[str, object]],
    *,
    visible_entities: Mapping[str, str],
    character_id: str,
) -> SafePayload:
    """Use release visibility, not character knowledge, to project public holdings and labels."""
    rows: list[dict[str, object]] = []
    if character_id not in visible_entities:
        return owned_payload({"character_id": character_id, "items": []})
    for item in holdings:
        if (
            item["visibility"] != "player_known"
            or not item["quantity"]
            or character_id not in (item["owner_id"], item["holder_id"])
        ):
            continue
        rows.append(
            {
                "holding_id": item["holding_id"],
                "name": item["name"],
                "kind": item["kind"],
                "quantity": item["quantity"],
                "owner": visible_entities.get(str(item["owner_id"]), "Unknown"),
                "holder": visible_entities.get(str(item["holder_id"]), "Unknown"),
                "carried": item["holder_id"] == character_id,
                "owned": item["owner_id"] == character_id,
            }
        )
    return owned_payload({"character_id": character_id, "items": rows})
