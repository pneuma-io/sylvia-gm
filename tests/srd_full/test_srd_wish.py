"""Wish pays once and reuses actual child effects without granting their casting costs."""

import hashlib
import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_wish import (
    COPIES,
    PROFILE,
    SOURCE_PAGE_SHA256,
    bind_runtime_consumers,
    extend_runtime,
    schema,
)

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

SRD = cards.SRD
PACKAGE = Path(
    os.environ.get(
        "FC14_WISH_PACKAGE", str(Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1")
    )
)
NATIVE_SHA256 = "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


@lru_cache
def _runtime():
    """Selected effect adapter on genuine admitted native bytes, without family regeneration."""
    raw = (PACKAGE / "runtime/ruleset.yaml").read_bytes()
    if "FC14_WISH_PACKAGE" in os.environ:
        assert hashlib.sha256(raw).hexdigest() == NATIVE_SHA256
    before = yaml.load(raw, Loader=yaml.CSafeLoader)
    runtime = deepcopy(before)
    runtime["definitions"]["spells"]["wish"] = PROFILE
    requirements = cards._casting_headers()["wish"]
    runtime["definitions"]["spell_casting"]["wish"] = requirements
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["wish"] = schema()
    for name in ("cast_spell", "cast_spell_outside_combat"):
        next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")[
            "enum_values"
        ].append("wish")
    text = extend_runtime(yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False))
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    # The production final exploration owner derives the same effect dispatch.
    # Retain native outside permissions/costs and insert only the new owning branch.
    outside = runtime["tools"]["cast_spell_outside_combat"]
    cast = runtime["tools"]["cast_spell"]
    existing = {p["name"] for p in outside["parameters"]}
    outside["parameters"] += [deepcopy(p) for p in cast["parameters"] if p["name"] not in existing]
    (branch,) = [
        n
        for n in _walk(cast["effects"])
        if n.get("condition") == "_spell_cast_failed != true and _spell_definition.family == 'wish'"
    ]
    index = next(
        i
        for i, effect in enumerate(outside["effects"])
        if effect.get("set", {}).get("field") == "_card_slots_after"
    )
    outside["effects"].insert(index, deepcopy(branch))
    selected, todo = (
        set(),
        [
            "cast_spell",
            "cast_spell_outside_combat",
            "end_concentration",
            "advance_srd_lasting_time",
            "start_group_combat_encounter",
            "begin_combat_turn",
        ],
    )
    while todo:
        name = todo.pop()
        if name not in selected:
            assert name in runtime["tools"], name
            selected.add(name)
            todo += [
                n["call"]
                for n in _walk(runtime["tools"][name]["effects"])
                if isinstance(n.get("call"), str)
            ]
    active, finished = set(), set()

    def visit(name):
        assert name not in active, name
        if name in finished:
            return
        active.add(name)
        for n in _walk(runtime["tools"][name]["effects"]):
            if isinstance(n.get("call"), str):
                visit(n["call"])
        active.remove(name)
        finished.add(name)

    for name in selected:
        visit(name)
    runtime["tools"] = {n: t for n, t in runtime["tools"].items() if n in selected}
    encoded = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()
    return runtime, encoded, before


@lru_cache
def _mechanic(name):
    runtime, encoded, _ = _runtime()
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


@lru_cache
def _archive():
    runtime, encoded, _ = _runtime()
    primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [row for row in record["tools"] if row["name"] in runtime["tools"]]
    record["ui_surfaces"] = []
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    names = {row["name"] for row in record["tools"]}
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    declarations = json.loads(payload["content/content-declarations.json"])
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        for path in links.get("artifacts", []):
            if path not in payload:
                payload[path] = (PACKAGE / path).read_bytes().replace(b"\r\n", b"\n")
        if "tools" in links:
            links["tools"] = [name for name in links["tools"] if name in names]
        if "ui_surfaces" in links:
            links["ui_surfaces"] = []
    payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
    return bundled_packages._archive(
        (NormalizedPackageDraft(record, payload, primary.format_version), reference)
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: PACKAGE)
    monkeypatch.setattr(public, "ROOT", PACKAGE.parents[1])
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    original = _effect_executor._execute_effect
    budgets = {}

    def measured(effect, context, tool_executor=None):
        if context.budget is not None:
            budgets[id(context.budget)] = context.budget
        return original(effect, context, tool_executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", measured)
    yield
    if budgets:
        assert all(b.max_operations == 10000 and b.operations <= 10000 for b in budgets.values())
        print("Wish selected maximum operations", max(b.operations for b in budgets.values()))


def _case(copy, *, source="wizard"):
    state, data, parameters = cards._spell_case("wish")
    if source == "sorcerer":
        data["classes"] = [{"id": "sorcerer", "level": 17}]
        data["abilities"]["charisma"] = 18
        data["spellcasting_grants"] = [
            {"source": "sorcerer", "ability": "charisma", "spells": ["wish"]}
        ]
        data.pop("wizard_spellbook", None)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
            _entity("guard", [5, 0, 0], participant_ref="guard", creature_type="humanoid"),
            _entity("other", [10, 0, 0], participant_ref="other", creature_type="humanoid"),
        ),
    }
    parameters.update(
        wish_spell=copy,
        wish_target="guard",
        target="hero",
        target_distance=0,
        somatic_component_available=False,
        material_component_available=False,
        material_item_ids=[],
    )
    return state, data, parameters


def _commit(owner, tool, **parameters):
    try:
        result, command = _act(owner, tool, **parameters)
    except MechanicsRejected as error:
        raise AssertionError((tool, vars(error))) from error
    assert result.status is ActionStatus.COMMITTED, result.result
    return result, command


def test_source_and_exception_retain_exact_child_effect_bodies_and_repeat():
    runtime, encoded, before = _runtime()
    page = next(
        p
        for p in json.loads((PACKAGE / "content/corpus/spells.json").read_bytes())["page_records"]
        if p["page"] == 175
    )
    assert page["text_sha256"] == SOURCE_PAGE_SHA256
    for family, name in (
        ("spell_healing", "cast_spell_healing"),
        (
            "single_target_concentration_damage_resistance",
            "cast_spell_single_target_concentration_damage_resistance",
        ),
        ("multi_target_timed_actor_mobility", "cast_spell_timed_actor_mobility"),
    ):
        assert (
            runtime["tools"]["resolve_srd_wish_" + family]["effects"]
            == before["tools"][name]["effects"]
        )
        assert runtime["tools"][name] == before["tools"][name]
    repeated = yaml.load(extend_runtime(encoded.decode()), Loader=yaml.CSafeLoader)
    assert repeated == runtime
    assert yaml.load(bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader) == runtime
    assert (
        "wish_cast_id"
        not in _tool_schema(runtime["tools"]["cast_spell"], tool_name="cast_spell")["properties"]
    )


@pytest.mark.parametrize(
    "copy,source",
    [
        ("cure_wounds", "wizard"),
        ("healing_word", "sorcerer"),
        ("fly", "wizard"),
        ("stoneskin", "wizard"),
    ],
)
def test_paid_public_duplicate_reopens_retries_and_preserves_child_owner(
    tmp_path, monkeypatch, copy, source
):
    state, data, parameters = _case(copy, source=source)
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "wish.db"
    rolls = []
    ordinary_roll = _effect_executor.dice_roll

    def observe(sides, count=1, modifier=0):
        result = ordinary_roll(sides, count, modifier)
        rolls.append((sides, count, result))
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", observe)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(
            owner, "wish", target_overrides={"hp": 1, "max_hp": 100, "temp_hp": 7}
        )
        before = owner.snapshot("casting")
        cast, command = _commit(owner, "cast_spell_outside_combat", **parameters)
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        target = saved.state[f"{SRD}.entities"]["guard"]
        assert hero["resources"] == dict(before.state[f"{SRD}.entities"]["hero"]["resources"]) | {
            "spell_slot_9": 2
        }
        assert hero["prepared_spells"] == ["wish"]
        assert hero["spell_materials"] == []
        assert hero["last_wish_duplicate"]["spell"] == copy
        assert hero["last_wish_duplicate"]["spell_level"] == COPIES[copy][0]
        assert hero["last_wish_duplicate"]["stress"] is False
        assert hero["wish_duplicate_authorization"]["status"] == "consumed"
        assert saved.state[f"{SRD}.combat"] == before.state[f"{SRD}.combat"]
        assert cast.result["result_card"]["title"] == "Wish"
        assert cast.result["result_card"]["details"]
        assert len(rolls) == (1 if copy in {"cure_wounds", "healing_word"} else 0)
        if rolls:
            assert rolls[0][0:2] == (8 if copy == "cure_wounds" else 4, 2)
        if copy in {"cure_wounds", "healing_word"}:
            assert target["hp"] > 1 and target["temp_hp"] == 7
            assert "concentration_spell_id" not in hero or hero["concentration_spell_id"] is None
        else:
            assert hero["concentration_spell_id"] == copy
            assert hero["concentration_expires_tick"] == (200 if copy == "fly" else 1200)
            if copy == "fly":
                (record,) = [
                    r for r in saved.state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == copy
                ]
                assert record["cast_id"] == hero["last_wish_duplicate"]["cast_id"]
                assert record["spell_level"] == 3 and record["source_caster_id"] == "hero"
                assert target["spell_flying_speed"] == 60
            else:
                assert list(target["concentration_resistance_source_ids"]) == ["hero"]
                assert hero["concentration_spell_level"] == 4
        assert owner.execute_action(command) == cast
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == cast
        if copy in {"fly", "stoneskin"}:
            ended, end_command = _commit(owner, "end_concentration", caster_state="hero")
            finished = owner.snapshot("casting")
            assert finished.state[f"{SRD}.entities"]["hero"]["resources"] == hero["resources"]
            assert finished.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
            if copy == "fly":
                assert finished.state[f"{SRD}.entities"]["guard"]["spell_flying_speed"] == 0
            else:
                assert (
                    list(
                        finished.state[f"{SRD}.entities"]["guard"][
                            "concentration_resistance_source_ids"
                        ]
                    )
                    == []
                )
            assert owner.execute_action(end_command) == ended
            assert owner.snapshot("casting") == finished
        assert len(rolls) == (1 if copy in {"cure_wounds", "healing_word"} else 0)


def test_unsupported_duplicate_and_unpaid_private_exception_reject_atomically():
    state, data, parameters = _case("fireball")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="implemented Wish duplicate"):
        budget._run("cast_spell", state, participant_data=data, **parameters)
    assert state == before
    state, data, parameters = _case("cure_wounds")
    next(row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "guard")[
        "position"
    ] = [20, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="spell's range"):
        budget._run("cast_spell", state, participant_data=data, **parameters)
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="actual paid Wish"):
        budget._run(
            "cast_srd_wish_duplicate",
            state,
            caster_state="hero",
            target="guard",
            spell="cure_wounds",
            wish_cast_id="fabricated",
            target_willing=True,
            casting_ability="intelligence",
            casting_modifier=4,
            casting_attack_bonus=10,
            casting_save_dc=18,
            casting_scaling_level=17,
            concentration_save_modifier=0,
        )
    assert state == before


def test_real_combat_wish_charges_one_action_without_child_bonus_action(tmp_path, monkeypatch):
    state, data, parameters = _case("healing_word")
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "wish-combat.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "wish")
        _commit(owner, "start_group_combat_encounter", combatants=["hero", "guard", "other"])
        _commit(owner, "begin_combat_turn")
        before = owner.snapshot("casting")
        assert before.state[f"{SRD}.combat"]["active_actor"] == "hero"
        paid, command = _commit(owner, "cast_spell", **parameters)
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert saved.state[f"{SRD}.combat"]["normal_action_spent"] is True
        assert (
            saved.state[f"{SRD}.entities"]["hero"]["bonus_action_available"]
            == before.state[f"{SRD}.entities"]["hero"]["bonus_action_available"]
        )
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"] == dict(
            before.state[f"{SRD}.entities"]["hero"]["resources"]
        ) | {"spell_slot_9": 2}
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] > 1
        assert owner.execute_action(command) == paid and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == paid and owner.snapshot("casting") == saved


def test_copy_does_not_waive_a_later_ordinary_sibling_slot_or_component(tmp_path, monkeypatch):
    state, data, parameters = _case("fly")
    child_state, _, child_parameters = cards._spell_case("fly")
    child_materials = deepcopy(child_state[f"{SRD}.entities"]["hero"]["spell_materials"])
    data["spellcasting_grants"][0]["spells"].append("fly")
    data["wizard_spellbook"].append("fly")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("fly")
    hero["spell_materials"] = child_materials
    hero["inventory"] += [item["inventory_item"] for item in child_materials]
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "wish-sibling.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "wish")
        _commit(owner, "cast_spell_outside_combat", **parameters)
        copied = owner.snapshot("casting")
        assert copied.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
        assert copied.state[f"{SRD}.entities"]["hero"]["spell_materials"] == hero["spell_materials"]
        _commit(owner, "end_concentration", caster_state="hero")
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "cast_spell_outside_combat",
                **(
                    child_parameters
                    | {"material_component_available": False, "material_item_ids": []}
                ),
            )
        assert owner.snapshot("casting") == before
        ordinary, command = _commit(owner, "cast_spell_outside_combat", **child_parameters)
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"] == dict(
            before.state[f"{SRD}.entities"]["hero"]["resources"]
        ) | {"spell_slot_3": 2}
        assert ordinary.result["result_card"]["title"] == "Fly"
        assert owner.execute_action(command) == ordinary and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(command) == ordinary and owner.snapshot("casting") == saved
