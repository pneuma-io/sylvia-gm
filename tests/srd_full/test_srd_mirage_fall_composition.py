"""A paid Mirage floor loses support through genuine Dispel and saved falling owners."""

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
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_feather_fall_public as exact_payloads
from tests import test_srd_mirage_arcane as mirage
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_illusions import _grant_other_spell

SRD = cards.SRD
SCOPE = Path(os.environ["FC14_MIRAGE_SCOPE"]) if "FC14_MIRAGE_SCOPE" in os.environ else None
PACKAGE = Path(
    os.environ.get(
        "FC14_MIRAGE_PACKAGE", str(Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1")
    )
)
ENCODED = ((SCOPE / "runtime.yaml") if SCOPE else PACKAGE / "runtime/ruleset.yaml").read_bytes()
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
    if SCOPE is None:
        return bundled_packages.bundled_srd_archive()
    primary, reference = exact_payloads._linked_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [r for r in record["tools"] if r["name"] in RUNTIME["tools"]]
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(RUNTIME["tools"][row["name"]], tool_name=row["name"])
    record["ui_surfaces"] = []
    names = {r["name"] for r in record["tools"]}
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": ENCODED}
    declarations = json.loads(payload["content/content-declarations.json"])
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        if "tools" in links:
            links["tools"] = [n for n in links["tools"] if n in names]
        if "ui_surfaces" in links:
            links["ui_surfaces"] = []
    payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
    result = bundled_packages._archive(
        (NormalizedPackageDraft(record, payload, primary.format_version), reference)
    )
    (SCOPE / "selected-public-package.zip").write_bytes(result)
    return result


@pytest.fixture(autouse=True)
def actual_owners(monkeypatch):
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: PACKAGE)
    monkeypatch.setattr(public, "ROOT", PACKAGE.parents[1])
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: RUNTIME["definitions"]["spells"])
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)


def _commit(owner, name, **parameters):
    try:
        result, command = _act(owner, name, **parameters)
    except MechanicsRejected as error:
        raise AssertionError((name, vars(error))) from error
    assert result.status is ActionStatus.COMMITTED, result.result
    return result, command


def _actor(snapshot):
    return snapshot.state[f"{SRD}.entities"]["hero"]


def _body(snapshot, actor="hero"):
    return next(
        row
        for row in snapshot.state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["id"] == actor
    )


def _faller(snapshot):
    return snapshot.state[f"{SRD}.entities"]["guard"]


def _case():
    state, data, parameters = mirage._case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1000, max_hp=1000, temp_hp=0)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "hero")["position"] = [-10, 0, 0]
    next(row for row in scene["entities"] if row["id"] == "guard")["position"] = [-10, 3, 0]
    parameters["mirage_structures"] = [
        {
            "id": "saved-mirage-platform",
            "position": [0, 0, 20],
            "extent": [10, 10, 10],
            "description": "Raised stone platform",
            "tactile_kind": "stone",
            "blocks_movement": True,
            "ground": True,
        }
    ]
    other = {
        spell: _grant_other_spell(state, data, spell)
        for spell in ("dimension_door", "dispel_magic", "feather_fall")
    }
    other["dimension_door"].update(travel_destination=[0, 0, 30])
    other["dispel_magic"].update(
        slot_resource="spell_slot_7", recovery_choice="mirage", recovery_position=[-10, 0, 0]
    )
    for spell in ("dimension_door", "dispel_magic"):
        for key in ("targets", "target"):
            other[spell].pop(key, None)
    other["dimension_door"].update(targets=["guard"], travel_companion_destination=[0, 3, 30])
    other["feather_fall"].update(targets=["guard"])
    return state, data, parameters, other


@pytest.mark.parametrize(
    "feather", [False, True], ids=["actual-3d6-landing", "paid-feather-landing"]
)
def test_paid_mirage_public_dispel_allocates_one_saved_fall_and_lands_once(
    tmp_path, monkeypatch, feather
):
    state, data, parameters, other = _case()
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "mirage-fall.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(
            owner, "mirage_arcane", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        begun, begin_command = _commit(owner, "begin_srd_spell_cast", **_begin(parameters))
        _commit(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        cast, cast_command = _commit(owner, "cast_spell_outside_combat", **parameters)
        source = next(iter(owner.snapshot("casting").state[f"{SRD}.state"]["mirage_sources"]))
        platform = next(
            row
            for row in owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"]["geometry"]
            if row["id"] == "saved-mirage-platform"
        )
        assert platform["volume"] == {"kind": "box", "min": [-5, -5, 20], "max": [5, 5, 30]}
        assert _actor(owner.snapshot("casting"))["resources"]["spell_slot_7"] == 2
        transported, teleport_command = _commit(
            owner, "cast_spell_outside_combat", **other["dimension_door"]
        )
        elevated = owner.snapshot("casting")
        assert _body(elevated)["position"] == [0, 0, 30]
        assert _body(elevated, "guard")["position"] == [0, 3, 30]
        assert _actor(elevated)["resources"]["spell_slot_4"] == 2
        returning = other["dimension_door"] | {"travel_destination": [-10, 0, 0]}
        returning.pop("targets")
        returned, return_command = _commit(owner, "cast_spell_outside_combat", **returning)
        elevated = owner.snapshot("casting")
        assert _body(elevated)["position"] == [-10, 0, 0]
        assert _body(elevated, "guard")["position"] == [0, 3, 30]
        assert _actor(elevated)["resources"]["spell_slot_4"] == 1
        assert owner.execute_action(begin_command) == begun
        assert owner.execute_action(cast_command) == cast
        assert owner.execute_action(teleport_command) == transported
        assert owner.execute_action(return_command) == returned
        assert owner.snapshot("casting") == elevated
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == elevated
        dispel = other["dispel_magic"] | {"recovery_source_id": source}
        ended, dispel_command = _commit(owner, "cast_spell_outside_combat", **dispel)
        saved = owner.snapshot("casting")
        lasting = saved.state[f"{SRD}.state"]
        assert lasting["mirage_sources"][source]["installed"] is False
        assert lasting.get("has_mirage_pending_support_losses", False) is False
        assert lasting["mirage_pending_support_losses"] == []
        assert len(lasting["pending_srd_action_stack"]) == 1
        trigger = lasting["pending_srd_action_stack"][0]
        event = lasting["pending_srd_actions"][trigger]
        assert event["kind"] == "scene_fall" and event["status"] == "open"
        actual = event["parameters"]["falling_events"][0]
        assert actual["actor_id"] == "guard" and actual["distance"] == 30
        assert actual["ground_id"] == "floor" and actual["ground_position"] == [0, 3, 0]
        assert event["context"]["mirage_support_receipts"][0]["mirage_id"] == source
        assert _faller(saved)["hp"] == 1000 and _body(saved, "guard")["position"] == [0, 3, 30]
        assert _actor(saved)["resources"]["spell_slot_7"] == 1
        with pytest.raises(MechanicsRejected, match="pending reaction"):
            _act(owner, "move_srd_scene_creature", actor_state="hero", destination=[-9, 0, 0])
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(dispel_command) == ended
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        if feather:
            reaction, reaction_command = _commit(
                owner,
                "cast_srd_reaction_spell",
                **(other["feather_fall"] | {"reaction_trigger_id": trigger}),
            )
            paid = owner.snapshot("casting")
            assert _actor(paid)["resources"]["spell_slot_1"] == 2
            assert _actor(paid)["reaction_available"] is False
            assert owner.execute_action(reaction_command) == reaction
            assert owner.snapshot("casting") == paid
        rolls = []
        ordinary = _effect_executor.dice_roll

        def observe(sides, count=1, modifier=0):
            result = ordinary(sides, count, modifier)
            rolls.append(result)
            return result

        monkeypatch.setattr(_effect_executor, "dice_roll", observe)
        finished, completion_command = _commit(
            owner, "complete_srd_pending_action", pending_action_id=trigger
        )
        assert finished.result["result_card"]["title"] == "Falling creatures"
        completed = owner.snapshot("casting")
        if feather:
            assert not rolls and _faller(completed)["hp"] == 1000
            _commit(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        else:
            assert len(rolls) == 1 and rolls[0].sides == 6 and rolls[0].count == 3
            assert _faller(completed)["hp"] == 1000 - rolls[0].total
            assert "prone" in _faller(completed)["conditions"]
        landed = owner.snapshot("casting")
        assert _body(landed, "guard")["position"] == [0, 3, 0]
        assert _faller(landed)["scene_fall"]["status"] == "landed"
        assert landed.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert owner.execute_action(completion_command) == finished
        assert owner.snapshot("casting") == landed
        assert _actor(landed)["resources"]["spell_slot_7"] == 1
        assert _actor(landed)["resources"]["spell_slot_4"] == 1
        _commit(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        final = owner.snapshot("casting")
        assert _faller(final)["hp"] == _faller(landed)["hp"] and _body(final, "guard")[
            "position"
        ] == [0, 3, 0]
        assert len(rolls) == (0 if feather else 1)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert owner.execute_action(dispel_command) == ended
        assert owner.execute_action(completion_command) == finished
        assert owner.snapshot("casting") == final


def test_genuine_paid_retirement_queue_blocks_ordinary_entries_before_actual_handoff():
    # Invoke the real private retirement callback, not a manufactured public action.
    state, data, parameters, other = _case()
    run = budget._run
    run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    run(
        "continue_srd_spell_cast",
        state,
        participant_data=data,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    ascent = other["dimension_door"] | {"travel_destination": [0, 0, 30]}
    ascent.pop("targets")
    run("cast_spell_outside_combat", state, participant_data=data, **ascent)
    source = next(iter(state[f"{SRD}.state"]["mirage_sources"]))
    run("retire_srd_mirage_source", state, mirage_id=source, reason="dispel")
    lasting = state[f"{SRD}.state"]
    assert lasting["has_mirage_pending_support_losses"] is True
    assert lasting["mirage_pending_support_losses"][0]["body"]["actor_id"] == "hero"
    assert not lasting.get("pending_srd_action_stack")
    saved = deepcopy(state)
    for name, choices in (
        ("move_srd_scene_creature", {"actor_state": "hero", "destination": [1, 0, 30]}),
        ("advance_srd_lasting_time", {"elapsed_rounds": 1}),
        ("begin_srd_spell_action", other["dispel_magic"] | {"recovery_source_id": source}),
    ):
        with pytest.raises(RulesetPreconditionRejected, match="queued Mirage support loss"):
            run(name, state, participant_data=data, **choices)
        assert state == saved
    run("handoff_srd_mirage_support_losses", state)
    lasting = state[f"{SRD}.state"]
    assert lasting["mirage_pending_support_losses"] == []
    action = lasting["pending_srd_action_stack"][0]
    assert lasting["pending_srd_actions"][action]["kind"] == "scene_fall"
    assert (
        lasting["pending_srd_actions"][action]["parameters"]["falling_events"][0]["distance"] == 30
    )
    assert (
        state[f"{SRD}.entities"]["hero"]["resources"]
        == saved[f"{SRD}.entities"]["hero"]["resources"]
    )
    completed = deepcopy(state)
    run("handoff_srd_mirage_support_losses", state)
    assert state == completed
