"""The original physical PC body receives native saves without a participant alias."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_astral_projection as original
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run


class Rolls:
    def __init__(self):
        self.sides = []

    def randint(self, _a, sides):
        self.sides.append(sides)
        return 5


def test_paid_physical_pc_snapshot_receives_real_native_save_and_cannot_act(monkeypatch):
    parent = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-astral-pc-receiving/retained-native-parent.yaml")
    ).read_bytes()
    assert sha256(parent).hexdigest() == (
        "fe066d1251fb874d6cb62f0fcb8eb925d4db40d56df0a3c8a8831158ce2624bc"
    )
    parsed = _load_runtime(parent.decode())
    monkeypatch.setattr(original, "source_runtime", lambda: (deepcopy(parsed), parent))
    original._runtime.cache_clear()
    original._mechanic.cache_clear()
    monkeypatch.setattr(budget, "_mechanic", original._mechanic)
    monkeypatch.setattr(
        cards, "_spell_definitions", lambda: original._runtime()[0]["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    state, data, params = original._case()
    data["abilities"]["constitution"] = 14
    data["saving_throw_proficiencies"] = ["intelligence", "wisdom"]
    original._paid_hour(state, data, params)
    actor = state[f"{SRD}.entities"]["hero"]
    source = state[f"{SRD}.state"]["astral_projection_sources"][
        actor["astral_projection_source_id"]
    ]
    body_id = source["body_id"]
    body = state[f"{SRD}.entities"][body_id]
    assert body_id != "hero" and body["astral_original_pc_id"] == "hero"
    assert body["ability_scores"] == data["abilities"]
    expected = 2 + (6 if "constitution" in data["saving_throw_proficiencies"] else 0)
    assert body["saving_throw_modifiers"]["constitution"] == expected
    assert body["saving_throw_modifiers"]["intelligence"] == (
        (data["abilities"]["intelligence"] - 10) // 2 + 6
    )
    before = deepcopy(actor)
    receipt = _run(
        "resolve_srd_condition_saving_throw",
        state,
        target=body_id,
        ability="constitution",
        condition="poisoned",
        source_save_dc=15,
    )
    context = receipt.result["context"]
    assert context["_combined_modifier"] == expected
    assert context["_adjusted_total"] == 5 + expected
    assert rolls.sides == [20]
    assert actor == before and body["conditions"] == ["unconscious"]
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="No receiving|further casting"):
        _run(
            "begin_srd_spell_cast",
            state,
            participant_data=data,
            **(original._begin_values(params) | {"caster_state": body_id}),
        )
    assert state == saved
