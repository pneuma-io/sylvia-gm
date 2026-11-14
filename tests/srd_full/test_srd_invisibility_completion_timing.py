"""Real public casting/attack journeys keep live Invisible sources until their actual event."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_feather_fall_public as exact_payloads
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_begin_timing as accepted
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_extended_casting import _begin as longer_parameters
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_reaction_windows import _parameters as weapon_parameters
from tests.srd_full.test_srd_spell_wave3_information import _entity

OUTPUT = Path(os.environ["FC14_INVIS_SCOPE"]) if "FC14_INVIS_SCOPE" in os.environ else None
PACKAGE = Path(
    os.environ.get(
        "FC14_INVIS_PACKAGE", str(Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1")
    )
)
LABEL = os.environ.get("FC14_INVIS_LABEL", "after")
ENCODED = (
    (OUTPUT / (LABEL + "-runtime.yaml")) if OUTPUT else (PACKAGE / "runtime/ruleset.yaml")
).read_bytes()
RUNTIME = _load_runtime(ENCODED)


@lru_cache
def _mechanic(name):
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=ENCODED,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(RUNTIME["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@lru_cache
def _archive():
    if OUTPUT is None:
        return bundled_packages.bundled_srd_archive()
    primary, reference = exact_payloads._linked_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [r for r in record["tools"] if r["name"] in RUNTIME["tools"]]
    record["ui_surfaces"] = []
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": ENCODED}
    declarations = json.loads(payload["content/content-declarations.json"])
    names = {r["name"] for r in record["tools"]}
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        if "tools" in links:
            links["tools"] = [n for n in links["tools"] if n in names]
        if "ui_surfaces" in links:
            links["ui_surfaces"] = []
    payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
    archive = bundled_packages._archive(
        (NormalizedPackageDraft(record, payload, primary.format_version), reference)
    )
    (OUTPUT / (LABEL + "-public-package.zip")).write_bytes(archive)
    return archive


@pytest.fixture(autouse=True)
def real_owners(monkeypatch):
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: PACKAGE)
    monkeypatch.setattr(public, "ROOT", PACKAGE.parents[1])
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: RUNTIME["definitions"]["spells"])
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)


def _source_records(state, spell="invisibility"):
    return [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == spell]


def _prepare_caster(state, actor, spells):
    combat = deepcopy(state[f"{SRD}.combat"])
    state[f"{SRD}.combat"].update(turn_active=False, encounter_active=False)
    if not any(e["id"] == actor for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]):
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity(actor, [0, 5, 0], participant_ref=actor)
        )
    supplies = [
        item
        for spell in spells
        for item in cards._spell_case(spell)[0][f"{SRD}.entities"]["hero"]["spell_materials"]
    ]
    _run(
        "prepare_srd_creature",
        state,
        creature_id=actor,
        name="Actual test wizard",
        source="Synthetic wizard: AC13 HP1000 Speed30 Int3 attack7 DC15 Con0.",
        armor_class=13,
        hit_points=1000,
        speed=30,
        initiative_modifier=-10,
        size_rank=2,
        constitution_save=0,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        inventory=[m["inventory_item"] for m in supplies],
        spell_materials=supplies,
        spells=[{"spell": s, "uses_per_day": 2} for s in spells],
    )
    state[f"{SRD}.combat"] = combat


def _case(spell="false_life", source="invisibility", *, native=False, see=False, slow=False):
    state, data, params = accepted._case(spell, slow=slow, invisible=source, native=native)
    # Actual fixed synthetic guard facts make the low real attack a miss, not a hit verdict.
    combat = deepcopy(state[f"{SRD}.combat"])
    state[f"{SRD}.combat"].update(turn_active=False, encounter_active=False)
    del state[f"{SRD}.entities"]["guard"]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="guard",
        name="Armored test guard",
        source="Synthetic guard: AC20 HP1000 Speed30 Medium Con0.",
        armor_class=20,
        hit_points=1000,
        speed=30,
        initiative_modifier=-10,
        size_rank=2,
        constitution_save=0,
    )
    state[f"{SRD}.combat"] = combat
    if "mage" not in state[f"{SRD}.entities"]:
        _prepare_caster(state, "mage", ["counterspell", "see_invisibility"])
    if see:
        combat = deepcopy(state[f"{SRD}.combat"])
        state[f"{SRD}.combat"].update(turn_active=False, encounter_active=False)
        parameters = deepcopy(cards._spell_case("see_invisibility")[2])
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters.update(
            caster_state="mage",
            target="mage",
            targets=["mage"],
            target_distance=0,
            targets_max_distance=0,
        )
        parameters["material_item_ids"] = [
            m["id"] for m in state[f"{SRD}.entities"]["mage"]["spell_materials"]
        ]
        _run("cast_spell_outside_combat", state, **parameters)
        state[f"{SRD}.combat"] = combat
    return state, data, params


def _campaign(owner, monkeypatch, spell, case):
    monkeypatch.setattr(public, "_spell_case", lambda _: case)
    public._create_spell_campaign(
        owner, spell, target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
    )
    result, _ = _act(
        owner,
        "start_combat_encounter_entities",
        first_actor="hero",
        second_actor="guard",
        first_surprised=False,
        second_surprised=False,
        tie_winner="first",
    )
    assert result.status is ActionStatus.COMMITTED, result.result
    result, _ = _act(owner, "begin_combat_turn")
    assert result.status is ActionStatus.COMMITTED, result.result
    assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "hero"


@pytest.mark.parametrize(
    "source,native", [("invisibility", False), ("greater_invisibility", False), (None, True)]
)
def test_pending_cast_live_source_reopen_then_committed_completion(
    source, native, tmp_path, monkeypatch
):
    case = _case(source=source, native=native)
    database = tmp_path / "pending-live.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch, "false_life", case)
        before = owner.snapshot("casting")
        begun, command = _act(owner, "begin_srd_spell_action", **case[2])
        assert begun.status is ActionStatus.COMMITTED, begun.result
        saved = owner.snapshot("casting")
        assert "invisible" in saved.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert (
            saved.state[f"{SRD}.entities"]["hero"]["resources"]
            == before.state[f"{SRD}.entities"]["hero"]["resources"]
        )
        event = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert owner.execute_action(command) == begun
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        finished, command = _act(owner, "complete_srd_pending_action", pending_action_id=event)
        assert finished.status is ActionStatus.COMMITTED, finished.result
        final = owner.snapshot("casting")
        assert ("invisible" in final.state[f"{SRD}.entities"]["hero"]["conditions"]) is (
            source != "invisibility"
        )
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert owner.execute_action(command) == finished
        assert owner.snapshot("casting") == final


@pytest.mark.parametrize("spell_attack", [True, False], ids=["real-spell-miss", "real-weapon-miss"])
def test_live_unseen_source_grants_first_actual_roll_then_miss_ends_it(
    spell_attack, tmp_path, monkeypatch
):
    case = _case("fire_bolt")
    if not spell_attack:
        case[0][f"{SRD}.entities"]["hero"]["inventory"].append("quarterstaff")
        case[1]["weapon_proficiencies"] = ["simple_weapons"]
    # Admitted enemy bodies must be beyond the five-foot ranged-attack penalty.
    for row in case[0][f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] == "mage":
            row["position"] = [0, 10, 0]
        elif row["id"] == "hidden":
            row["position"] = [0, -10, 0]
        elif spell_attack and row["id"] == "guard":
            row["position"] = [25, 0, 0]
    database = tmp_path / "real-roll.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch, "fire_bolt", case)
        monkeypatch.setattr(dice, "_rng", Random(2))
        actual_advantage_rolls = []
        original_advantage = dice.advantage

        def observe_advantage(modifier):
            actual = original_advantage(modifier)
            actual_advantage_rolls.append(actual.to_dict())
            return actual

        monkeypatch.setattr(dice, "advantage", observe_advantage)
        if spell_attack:
            begun, _ = _act(owner, "begin_srd_spell_action", **case[2])
            assert begun.status is ActionStatus.COMMITTED, begun.result
            assert (
                "invisible"
                in owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["conditions"]
            )
            event = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
            rolled, command = _act(owner, "complete_srd_pending_action", pending_action_id=event)
            assert rolled.status is ActionStatus.COMMITTED, rolled.result
            assert len(actual_advantage_rolls) == 1
            actual = actual_advantage_rolls[0]
            card = rolled.result["result_card"]
            assert card["title"] == "Fire Bolt"
            assert any(row["value"] == "Miss; damage 0" for row in card["summary"])
        else:
            parameters = weapon_parameters()
            parameters.pop("actor_state")  # Hidden binding comes from the admitted actor.
            parameters["weapon"] = "quarterstaff"
            # Neutral legacy defaults cannot supply unseen Advantage; the live owner must.
            rolled, command = _act(owner, "begin_srd_character_attack", **parameters)
            assert rolled.status is ActionStatus.COMMITTED, rolled.result
            event = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
            saved = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_actions"][event]
            actual = saved["attack_roll"]
            assert saved["attack_outcome"] == "miss"
        assert len(actual["results"]) == 2 and actual["total"] == max(actual["results"])
        saved = owner.snapshot("casting")
        assert "invisible" not in saved.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert owner.execute_action(command) == rolled
        assert owner.snapshot("casting") == saved


def test_unseen_pending_caster_cannot_be_counterspelled_without_real_sense(tmp_path, monkeypatch):
    case = _case()
    with AuthorityOwner.open(tmp_path / "unseen-counter.db", "casting-owner") as owner:
        _campaign(owner, monkeypatch, "false_life", case)
        begun, _ = _act(owner, "begin_srd_spell_action", **case[2])
        assert begun.status is ActionStatus.COMMITTED, begun.result
        before = owner.snapshot("casting")
        event = before.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        with pytest.raises(MechanicsRejected) as rejected:
            _act(
                owner,
                "cast_srd_reaction_spell",
                spell="counterspell",
                caster_state="mage",
                reaction_trigger_id=event,
                somatic_component_available=True,
            )
        assert rejected.value.state_changed is False
        assert owner.snapshot("casting") == before
        assert "invisible" in before.state[f"{SRD}.entities"]["hero"]["conditions"]


def test_actual_see_invisibility_counterspell_keeps_source_until_failed_completion(
    tmp_path, monkeypatch
):
    case = _case(see=True)
    database = tmp_path / "seen-counter.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch, "false_life", case)
        begun, _ = _act(owner, "begin_srd_spell_action", **case[2])
        assert begun.status is ActionStatus.COMMITTED, begun.result
        saved = owner.snapshot("casting")
        event = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        monkeypatch.setattr(dice, "_rng", Random(2))
        countered, command = _act(
            owner,
            "cast_srd_reaction_spell",
            spell="counterspell",
            caster_state="mage",
            reaction_trigger_id=event,
            somatic_component_available=True,
        )
        assert countered.status is ActionStatus.COMMITTED, countered.result
        saved = owner.snapshot("casting")
        actual_countered = saved.state[f"{SRD}.state"]["pending_srd_actions"][event]
        assert actual_countered["status"] == "lost" and actual_countered["countered_by"] == "mage"
        assert "invisible" in saved.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["counterspell"] == 1
        assert owner.execute_action(command) == countered
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        completed, command = _act(owner, "complete_srd_pending_action", pending_action_id=event)
        assert completed.status is ActionStatus.COMMITTED, completed.result
        final = owner.snapshot("casting")
        assert "invisible" not in final.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 3
        assert (
            final.state[f"{SRD}.entities"]["hero"]["temp_hp"]
            == saved.state[f"{SRD}.entities"]["hero"]["temp_hp"]
        )
        assert owner.execute_action(command) == completed
        assert owner.snapshot("casting") == final


@pytest.mark.parametrize("native", [False, True])
def test_ally_old_source_self_recast_retains_actual_new_invisibility_source(
    tmp_path, monkeypatch, native
):
    case = _case("invisibility", native=native)
    case[2].update(target="hero", targets=["hero"])
    state = case[0]
    _prepare_caster(state, "sibling-caster", ["invisibility"])
    combat = deepcopy(state[f"{SRD}.combat"])
    state[f"{SRD}.combat"].update(turn_active=False, encounter_active=False)
    _run(
        "cast_spell_outside_combat",
        state,
        caster_state="sibling-caster",
        spell="invisibility",
        target="other",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=[
            m["id"] for m in state[f"{SRD}.entities"]["sibling-caster"]["spell_materials"]
        ],
    )
    state[f"{SRD}.combat"] = combat
    sibling = [r for r in _source_records(state) if r.get("caster") == "sibling-caster"]
    assert sibling
    database = tmp_path / "self-recast.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch, "invisibility", case)
        old = [
            r for r in _source_records(owner.snapshot("casting").state) if r["caster"] == "hidden"
        ]
        assert old and all(r["caster"] == "hidden" for r in old)
        begun, command = _act(owner, "begin_srd_spell_action", **case[2])
        assert begun.status is ActionStatus.COMMITTED, begun.result
        saved = owner.snapshot("casting")
        assert [r for r in _source_records(saved.state) if r["caster"] == "hidden"] == old
        event = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert owner.execute_action(command) == begun
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        completed, command = _act(owner, "complete_srd_pending_action", pending_action_id=event)
        assert completed.status is ActionStatus.COMMITTED, completed.result
        final = owner.snapshot("casting")
        assert [
            r for r in _source_records(final.state) if r["caster"] == "sibling-caster"
        ] == sibling
        records = [r for r in _source_records(final.state) if r["caster"] != "sibling-caster"]
        assert records and all(r["caster"] == "hero" for r in records)
        assert all(
            r["source"] == "hero" and r["instance"] not in {x["instance"] for x in old}
            for r in records
        )
        assert "invisible" in final.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert owner.execute_action(command) == completed
        assert owner.snapshot("casting") == final


def test_real_slow_failure_completes_ends_source_and_costs_once_without_reroll(
    tmp_path, monkeypatch
):
    case = _case("stoneskin", slow=True)

    class ActualDraws(Random):
        def __init__(self, seed):
            super().__init__(seed)
            self.draws = []

        def randint(self, low, high):
            value = super().randint(low, high)
            self.draws.append((low, high, value))
            return value

    database = tmp_path / "slow-failure.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _campaign(owner, monkeypatch, "stoneskin", case)
        before = owner.snapshot("casting")
        rng = ActualDraws(2)
        monkeypatch.setattr(dice, "_rng", rng)
        failed, command = _act(owner, "begin_srd_spell_action", **case[2])
        assert failed.status is ActionStatus.COMMITTED, failed.result
        final = owner.snapshot("casting")
        assert rng.draws == [(1, 4, 1)]
        assert not final.state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert "invisible" not in final.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert (
            final.state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"]
            == before.state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] - 1
        )
        assert "Casting failed" in str(failed.result["result_card"])
        assert owner.execute_action(command) == failed
        assert rng.draws == [(1, 4, 1)]
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert owner.execute_action(command) == failed
        assert rng.draws == [(1, 4, 1)]


@pytest.mark.parametrize(
    "failed", [False, True], ids=["real-longer-completion", "real-longer-slow-failure"]
)
def test_longer_cast_keeps_live_source_until_actual_cast_or_failed_completion(
    tmp_path, monkeypatch, failed
):
    case = _case("foresight", slow=failed)
    case[0][f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    database = tmp_path / "longer-cast.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        monkeypatch.setattr(public, "_spell_case", lambda _: case)
        public._create_spell_campaign(owner, "foresight")
        monkeypatch.setattr(dice, "_rng", Random(2 if failed else 0))
        begun, command = _act(owner, "begin_srd_spell_cast", **longer_parameters(case[2]))
        assert begun.status is ActionStatus.COMMITTED, begun.result
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert ("invisible" in hero["conditions"]) is (not failed)
        assert hero["resources"]["spell_slot_9"] == (2 if failed else 3)
        assert owner.execute_action(command) == begun
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        if failed:
            assert hero.get("extended_casting") is None
            assert "Slow: Somatic gestures failed" in str(begun.result["result_card"])
            return
        stack = saved.state[f"{SRD}.state"].get("pending_srd_action_stack", [])
        if stack:
            closed, command = _act(
                owner, "complete_srd_pending_action", pending_action_id=stack[-1]
            )
            assert closed.status is ActionStatus.COMMITTED, closed.result
            assert (
                "invisible"
                in owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["conditions"]
            )
            assert owner.execute_action(command) == closed
        progressed, command = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progressed.status is ActionStatus.COMMITTED, progressed.result
        ready = owner.snapshot("casting")
        assert "invisible" in ready.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert ready.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 3
        assert owner.execute_action(command) == progressed
        assert owner.snapshot("casting") == ready
        finished, command = _act(owner, "cast_spell_outside_combat", **case[2])
        assert finished.status is ActionStatus.COMMITTED, finished.result
        final = owner.snapshot("casting")
        assert "invisible" not in final.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
        assert owner.execute_action(command) == finished
        assert owner.snapshot("casting") == final
