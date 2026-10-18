"""The neutral Initiative primitive retains the actual roll alongside ordered totals."""

from random import Random
from types import SimpleNamespace

import pytest

from sylvia_gm import dice
from sylvia_gm.runtime._initiative import roll_initiative


@pytest.mark.parametrize("expression,sides,inline_modifier", [("d20", 20, 0), ("2d6 + 2", 6, 2)])
def test_initiative_retains_dice_and_declared_labels_without_changing_order(
    monkeypatch, expression, sides, inline_modifier,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    entities = {"one": {"name": "First", "bonus": 3}, "two": {"name": "Second", "bonus": -2}}
    context = SimpleNamespace(entities=entities)
    result = roll_initiative(["one", "two"], expression, None, context,
                             modifier_field="bonus", label_field="name")
    assert [entry["initiative_result"] for entry in result] == sorted(
        (entry["initiative_result"] for entry in result), reverse=True,
    )
    for entry in result:
        entity = entities[entry["entity_id"]]
        assert entry["label"] == entity["name"]
        assert entry["roll"]["sides"] == sides
        assert entry["roll"]["total"] == sum(entry["roll"]["results"])
        assert entry["modifier"] == entity["bonus"] + inline_modifier
        assert entry["initiative_result"] == entry["roll"]["total"] + entry["modifier"]
    entities["one"]["bonus"] = 99
    assert next(e for e in result if e["entity_id"] == "one")["modifier"] == 3 + inline_modifier


def test_fixed_initiative_does_not_invent_dice():
    result = roll_initiative(["one"], "7", None, SimpleNamespace(entities={"one": {}}))
    assert result[0]["initiative_result"] == 7
    assert result[0]["roll"] is None
