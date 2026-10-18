"""The fallback's shared invariants do not depend on a Ruleset or UI."""

import pytest

from sylvia_gm.domain.inventory import (
    inventory_holding_schema,
    player_inventory,
    transfer_holding,
    validate_holdings,
)
from sylvia_gm.domain.package_library import _validate_character_update_declaration

ENTITIES = frozenset({"mira", "waystation", "chest", "hidden-owner"})


@pytest.mark.parametrize("format_name", ["holdings", "string_list"])
def test_ruleset_inventory_binding_validates_the_projection_shape(format_name):
    from copy import deepcopy

    item_schema = inventory_holding_schema() if format_name == "holdings" else {"type": "string"}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["updates"],
        "x-sylvia-gm-character-update": {"namespace": "example.rules", "key": "characters"},
        "x-sylvia-gm-inventory": {"field": "pack", "format": format_name},
        "properties": {
            "updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["participant_id", "fields"],
                    "properties": {
                        "participant_id": {"type": "string"},
                        "fields": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "pack": {"type": "array", "items": item_schema},
                            },
                        },
                    },
                },
            }
        },
    }
    _validate_character_update_declaration(schema, "example.rules")
    invalid = deepcopy(schema)
    invalid["properties"]["updates"]["items"]["properties"]["fields"]["properties"]["pack"][
        "items"
    ] = {
        "type": "object",
        "properties": {},
    }
    with pytest.raises(ValueError, match="inventory|Inventory"):
        _validate_character_update_declaration(invalid, "example.rules")
    missing = deepcopy(schema)
    missing.pop("x-sylvia-gm-character-update")
    with pytest.raises(ValueError, match="shared character"):
        _validate_character_update_declaration(missing, "example.rules")


def holding(**changes):
    return {
        "holding_id": "arrows-store",
        "item_id": "ordinary-arrow",
        "name": "Arrow",
        "kind": "item",
        "stackable": True,
        "quantity": 5,
        "owner_id": "waystation",
        "holder_id": "chest",
        "visibility": "player_known",
        "known_by": ["player"],
        **changes,
    }


def test_partial_loan_return_and_sale_preserve_exact_quantities():
    original = [holding()]
    loan = transfer_holding(
        original,
        source_id="arrows-store",
        destination_id="arrows-mira",
        quantity=2,
        holder_id="mira",
        entity_ids=ENTITIES,
    )
    assert [row["quantity"] for row in loan] == [3, 2]
    assert all(row["owner_id"] == "waystation" for row in loan)
    assert original[0]["quantity"] == 5
    returned = transfer_holding(
        loan,
        source_id="arrows-mira",
        destination_id="arrows-store",
        quantity=1,
        holder_id="chest",
        entity_ids=ENTITIES,
    )
    assert [row["quantity"] for row in returned] == [4, 1]
    sold = transfer_holding(
        returned,
        source_id="arrows-mira",
        destination_id="arrows-mira",
        quantity=1,
        holder_id="mira",
        owner_id="mira",
        entity_ids=ENTITIES,
    )
    assert sold[1]["owner_id"] == "mira"
    assert sum(row["quantity"] for row in sold) == 5


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": -1},
        {"quantity": True},
        {"quantity": 1.5},
        {"quantity": 2**53},
        {"owner_id": "missing"},
        {"holder_id": "missing"},
        {"known_by": "player"},
        {"known_by": ["player", "player"]},
        {"stackable": False},
        {"kind": "currency", "stackable": False},
        {"extra": "unvalidated"},
    ],
)
def test_malformed_holdings_rejected(changes):
    with pytest.raises(ValueError):
        validate_holdings([holding(**changes)], entity_ids=ENTITIES)


def test_distinct_objects_and_stacks_cannot_be_duplicated():
    unique = holding(item_id="engraved-blade", stackable=False, quantity=1)
    with pytest.raises(ValueError, match="distinct object"):
        validate_holdings(
            [unique, {**unique, "holding_id": "second", "holder_id": "mira"}], entity_ids=ENTITIES
        )
    with pytest.raises(ValueError, match="must stack"):
        validate_holdings([holding(), holding(holding_id="second")], entity_ids=ENTITIES)
    moved = transfer_holding(
        [unique],
        source_id="arrows-store",
        destination_id="blade-mira",
        quantity=1,
        holder_id="mira",
        entity_ids=ENTITIES,
    )
    assert [row["quantity"] for row in moved] == [0, 1]
    assert moved[1]["item_id"] == "engraved-blade"


def test_named_currencies_never_convert_and_failed_transfer_leaves_input_unchanged():
    holdings = [
        holding(item_id="gold", name="Gold", kind="currency"),
        holding(
            holding_id="silver-mira",
            item_id="silver",
            name="Silver",
            kind="currency",
            holder_id="mira",
        ),
    ]
    with pytest.raises(ValueError, match="cannot convert"):
        transfer_holding(
            holdings,
            source_id="arrows-store",
            destination_id="silver-mira",
            quantity=1,
            holder_id="mira",
            entity_ids=ENTITIES,
        )
    assert [row["quantity"] for row in holdings] == [5, 5]


@pytest.mark.parametrize("quantity", [0, -1, 6, True, 0.5])
def test_invalid_transfer_rejected(quantity):
    with pytest.raises(ValueError):
        transfer_holding(
            [holding()],
            source_id="arrows-store",
            destination_id="arrows-mira",
            quantity=quantity,
            holder_id="mira",
            entity_ids=ENTITIES,
        )


def test_projection_shows_loans_and_owned_storage_without_private_details():
    rows = [
        holding(holder_id="mira", owner_id="hidden-owner"),
        holding(holding_id="stored", item_id="bedroll", name="Bedroll", owner_id="mira"),
        holding(
            holding_id="secret",
            item_id="secret",
            name="Secret",
            holder_id="mira",
            visibility="gm_private",
        ),
        holding(
            holding_id="unreleased",
            item_id="unreleased",
            name="Unreleased",
            holder_id="mira",
            visibility="gm_private",
            known_by=["mira"],
        ),
        holding(holding_id="npc-only"),
    ]
    result = player_inventory(
        rows,
        visible_entities={"mira": "Mira", "chest": "Store chest"},
        character_id="mira",
    )
    assert [row["name"] for row in result["items"]] == ["Arrow", "Bedroll"]
    assert result["items"][0]["owner"] == "Unknown"
    assert result["items"][0]["carried"] and not result["items"][0]["owned"]
    assert result["items"][1]["holder"] == "Store chest"
    assert "hidden-owner" not in str(result)
    assert "known_by" not in str(result)


@pytest.mark.parametrize("audience", [[], ["mira"], ["player"], ["mira", "player"]])
def test_player_visibility_is_independent_of_character_knowledge(audience):
    public = holding(holder_id="mira", known_by=audience)
    secret = holding(holding_id="secret", holder_id="mira", visibility="gm_private",
                     known_by=audience)
    result = player_inventory(
        [public, secret], visible_entities={"mira": "Mira"}, character_id="mira",
    )
    assert [row["holding_id"] for row in result["items"]] == [public["holding_id"]]
    assert public["known_by"] == audience and secret["known_by"] == audience
