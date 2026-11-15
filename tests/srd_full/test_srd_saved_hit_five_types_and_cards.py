"""One genuine five-type hit reflects once; native cards retain honest whole-type receipts."""

import ast
import hashlib
import importlib.util
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import srd_attack_hit_riders as owner
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_parameter_declarations import compact_runtime
from scripts.srd_full.srd_timed_actor_defenses import _call, _parameter, _set, _tool, _when

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_saved_hit_type_receipts as types
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_hit_riders import BEGIN, Rolls, _begin_hit, _finish
from tests.srd_full.test_srd_spell_damage_consumers import _record_rolls
from tests.srd_full.test_srd_spell_damage_riders import _cast
from tests.srd_full.test_srd_warding_bond_damage import _ring

REACTION_SOURCE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-saved-hit-five-types-and-cards/srd_reaction_spells.py")
)
REACTION_HASH = "d6c4a21ca639294b10e999366b3b7257ffd44996418097ad454d94ef640edf84"
PRESENTERS = {
    "_insect_poison_damage_roll != null": "present_srd_native_poison_components",
    "_ghoul_necrotic_damage_roll != null": "present_srd_native_ghoul_components",
}


@lru_cache
def _native_card_branches():
    """Read only the exact actual Root card constructors, without importing its mutable peers."""
    raw = REACTION_SOURCE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REACTION_HASH
    tree = ast.parse(raw.decode("utf-8-sig"))
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_complete_window"
    )
    result = {}
    namespace = {"_call": _call, "_when": _when}
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_when"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in PRESENTERS
        ):
            expression = ast.Expression(node)
            result[node.args[0].value] = eval(
                compile(expression, str(REACTION_SOURCE), "eval"), namespace
            )
    assert set(result) == set(PRESENTERS)
    return result


def _presentation_tool(branch):
    """Exercise actual card effects with mechanical receipts, without another game packet."""
    branch = deepcopy(branch)
    owner._bind_component_cards([branch])
    return _tool(
        "Present genuine saved packet receipts through the exact native card branch.",
        [
            _parameter("receipts", "array", items={"type": "object"}),
            _parameter("first", "integer", required=False, default=None),
            _parameter("second", "integer", required=False, default=None),
            _parameter("rolls", "array", min_items=1, max_items=1, items={"type": "object"}),
            _parameter("incoming", "integer"),
        ],
        [
            _set("_pending_card_details", []),
            _set("_hit_type_receipts", "receipts"),
            *owner._nullable("_component_1_effective_damage", "first"),
            *owner._nullable("_component_2_effective_damage", "second"),
            _set("_insect_poison_damage_roll", "rolls[0]"),
            _set("_ghoul_necrotic_damage_roll", "rolls[0]"),
            _set("_insect_poison_damage_amount", "incoming"),
            _set("_ghoul_necrotic_damage_amount", "incoming"),
            branch,
        ],
    )


@lru_cache
def _runtime():
    runtime = deepcopy(types._runtime()[0])
    for condition, branch in _native_card_branches().items():
        runtime["tools"][PRESENTERS[condition]] = _presentation_tool(branch)
    return runtime, compact_runtime(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    ).encode()


@lru_cache
def _mechanic(name):
    # Use the established strict materializer with this owning graph and unchanged budgets.
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls(15, 3))


def test_real_paid_five_types_reflect_in_one_caster_packet_and_one_concentration_save(monkeypatch):
    state, data, smite = types._mixed_case("searing_smite", 2)
    data["classes"] = [
        {"id": "wizard", "level": 9},
        {"id": "paladin", "level": 5},
        {"id": "ranger", "level": 3},
        {"id": "cleric", "level": 3},
    ]
    data["level"] = 20
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    types._curse(state, data)
    mark = types._admit_spell(state, data, "hunters_mark")
    _cast(state, data, mark | {"target": "guard", "target_distance": 5})
    favor = types._admit_spell(state, data, "divine_favor")
    favor.pop("target")
    _run("cast_spell_outside_combat", state, participant_data=data, **favor)
    for actor in ("hero", "guard"):
        _ring(state, actor)
    bond = types._admit_spell(state, data, "warding_bond")
    bond["material_item_ids"] = ["hero_ring"]
    _run("cast_spell_outside_combat", state, participant_data=data, **bond)
    target, caster = (state[f"{SRD}.entities"][actor] for actor in ("guard", "hero"))
    target.update(temp_hp=40)
    caster.update(hp=100, max_hp=100, temp_hp=40, damage_resistances=["necrotic"])
    before_slot = caster["resources"]["spell_slot_2"]
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", current_actor="hero"
    )
    rolls = _record_rolls(monkeypatch)
    packets = types._observe_packets(monkeypatch)
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    child = _run(
        BEGIN, state, participant_data=data, **(smite | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    state = json.loads(json.dumps(state))
    _finish(state, data, child)
    result = _finish(state, data, hit)
    receipt = {r["damage_type"]: r for r in result["_hit_type_receipts"]}
    assert set(receipt) == {"piercing", "force", "radiant", "necrotic", "fire"}
    assert all(r["effective"] > 0 for r in receipt.values())
    target, caster = (state[f"{SRD}.entities"][actor] for actor in ("guard", "hero"))
    received = sum(r["effective"] for r in receipt.values())
    reflected = received - receipt["necrotic"]["effective"] + receipt["necrotic"]["effective"] // 2
    assert target["hp"] == caster["hp"] == 100
    assert target["temp_hp"] == 40 - received
    assert caster["temp_hp"] == 40 - reflected
    assert len(packets) == 2 and packets[1][0] == "apply_srd_reflected_warding_bond_damage"
    assert [p[1] for p in packets] == [received, reflected]
    assert [r.count for r in rolls if r.sides == 20] == [1, 1]
    assert caster["concentration_spell_id"] == "hunters_mark"
    assert caster["resources"]["spell_slot_2"] == before_slot - 1
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="does not exist"):
        _finish(state, data, hit)
    assert state == before


@pytest.mark.parametrize("condition", PRESENTERS)
def test_exact_native_component_cards_switch_only_when_individual_attribution_is_null(condition):
    original = deepcopy(_native_card_branches()[condition])
    bound = deepcopy(original)
    owner._bind_component_cards([bound])
    choices = [
        e
        for rows in owner._lists([bound])
        for e in rows
        if e.get("condition") == owner.CARD_CONDITION
    ]
    assert len(choices) == 1
    expected = next(
        e
        for e in original["on_true"]
        if e.get("params", {}).get("text") in owner.COMPONENT_CARD_TEXTS
    )
    assert choices[0]["on_false"] == [expected]
    assert owner._restore_damage_calls([bound]) == [original]
    assert bound["on_true"][0:2] == original["on_true"][0:2]
    assert bound["on_true"][-1] == original["on_true"][-1]

    # Card-only formatting inputs are factual receipts, not caller outcomes for game damage.
    kind = "poison" if "insect" in condition else "necrotic"
    receipts = [
        {"damage_type": "piercing", "amount": 3, "effective": 3, "component_count": 1},
        {"damage_type": kind, "amount": 3, "effective": 1, "component_count": 2},
    ]
    state = budget._state()
    text = _run(
        PRESENTERS[condition],
        state,
        receipts=receipts,
        first=3,
        second=None,
        rolls=[{"results": [1], "total": 1}],
        incoming=1,
    ).result["context"]["_pending_card_details"]
    assert "After defenses (whole types): 3 piercing; 1 " + kind + "." == text[-1]["value"]
    assert "None" not in text[-1]["value"] and "null" not in text[-1]["value"]
    assert (
        text[0]["value"]
        == ("Poison" if kind == "poison" else "Necrotic")
        + " dice 1; incoming 1 "
        + ("Poison" if kind == "poison" else "Necrotic")
        + "."
    )
    numeric = _run(
        PRESENTERS[condition],
        state,
        receipts=receipts,
        first=3,
        second=1,
        rolls=[{"results": [1], "total": 1}],
        incoming=1,
    ).result["context"]["_pending_card_details"]
    assert (
        numeric[-1]["value"]
        == "After defenses: 3 Piercing and 1 "
        + ("Poison" if kind == "poison" else "Necrotic")
        + "."
    )


def test_bound_change_preserves_exact_bond_receiving_effects_and_v2_upgrade():
    root = Path(__file__).resolve().parents[2]
    prior = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-saved-hit-five-types-and-cards/srd_attack_hit_riders.py")
    )
    assert (
        hashlib.sha256(prior.read_bytes()).hexdigest()
        == "119bb9d0a52d664df29ca64cb5e71a6e6a93857e20d87e923af878a7b7677389"
    )
    bond = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-saved-hit-five-types-and-cards/srd_warding_bond_damage.py")
    )
    assert (
        hashlib.sha256(bond.read_bytes()).hexdigest()
        == "99169c4f25a90b74657edc1d95fbbe6714ff8c90ecdb3ae4f5ee39ed9093f0fd"
    )
    current = (root / "scripts/srd_warding_bond_damage.py").read_text(encoding="utf-8")
    assert current.replace("max_items=5", "max_items=4", 1) == bond.read_text(encoding="utf-8")
    # The composed graph's strict setup already proves repeat and exact inverse equality.
    runtime = _runtime()[0]
    parameter = next(
        p
        for p in runtime["tools"]["apply_srd_reflected_warding_bond_damage"]["parameters"]
        if p["name"] == "damage_components"
    )
    assert parameter["max_items"] == 5
    spec = importlib.util.spec_from_file_location("scripts._immutable_after_hit_v2", prior)
    old_owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old_owner)
    current = yaml.dump(types._runtime()[0], Dumper=yaml.CSafeDumper, sort_keys=False)
    original = owner.restore_runtime(current)
    upgraded = owner.bind_runtime(old_owner.bind_runtime(original))
    assert yaml.load(upgraded, Loader=yaml.CSafeLoader) == types._runtime()[0]
