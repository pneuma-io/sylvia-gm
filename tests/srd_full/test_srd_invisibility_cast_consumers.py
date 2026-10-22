"""Ordinary Invisible ends from real accepted casting, attack rolls and sourced damage."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_invisibility_cast_consumers import bind_runtime_consumers
from scripts.srd_full.srd_spell_illumination import _casting_tool

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_extended_casting import _begin, _external_slow
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-invisibility-cast-consumers/input-1"))


@lru_cache
def _runtime(bound=True):
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    authored = _casting_tool()
    actual = runtime["tools"]["cast_spell_illumination_spell"]
    index = next(i for i, p in enumerate(actual["parameters"]) if p["name"] == "target_state")
    actual["parameters"][index] = deepcopy(
        next(p for p in authored["parameters"] if p["name"] == "target_state")
    )
    branch = next(
        e
        for e in actual["effects"]
        if e.get("condition") == "_light_profile.illumination_mode == 'invisible'"
    )
    index = next(
        i
        for i, e in enumerate(branch["on_true"])
        if e.get("condition") == "len(_invis_targets) == 0"
    )
    authored_branch = next(
        e
        for e in authored["effects"]
        if e.get("condition") == "_light_profile.illumination_mode == 'invisible'"
    )
    branch["on_true"][index] = deepcopy(
        next(
            e
            for e in authored_branch["on_true"]
            if e.get("condition") == "len(_invis_targets) == 0"
        )
    )
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    if bound:
        text = bind_runtime_consumers(text)
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
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
def _actual_owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def _case(spell="false_life"):
    state, data, params = cards._spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero"),
            _entity("mage", [0, 5, 0], participant_ref="mage"),
            _entity("guard", [20, 0, 0], participant_ref="guard"),
            _entity("other", [0, 10, 0], participant_ref="other"),
        ),
    }
    return state, data, params


def _prepare_invisible(state, caster_id="mage"):
    supplies = cards._spell_case("invisibility")[0][f"{SRD}.entities"]["hero"]["spell_materials"]
    _run(
        "prepare_srd_creature",
        state,
        creature_id=caster_id,
        name="Invisible mage",
        source="Synthetic prepared caster: AC12 HP30 Speed30 Int3 attack7 DC15.",
        armor_class=12,
        hit_points=30,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=0,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        inventory=[m["inventory_item"] for m in supplies],
        spell_materials=supplies,
        spells=[
            {"spell": "invisibility", "uses_per_day": 3},
            {"spell": "greater_invisibility", "uses_per_day": 3},
        ],
    )


def _invisible(state, spell="invisibility", target="hero"):
    if "mage" not in state[f"{SRD}.entities"]:
        _prepare_invisible(state)
    _run(
        "cast_spell_outside_combat",
        state,
        spell=spell,
        caster_state="mage",
        target=target,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=[m["id"] for m in state[f"{SRD}.entities"]["mage"]["spell_materials"]],
    )
    assert "invisible" in state[f"{SRD}.entities"][target]["conditions"]


def _is_invisible(state, target="hero"):
    return "invisible" in state[f"{SRD}.entities"][target]["conditions"]


def test_paid_ordinary_spell_ends_other_casters_invisibility_without_attack_or_damage():
    state, data, params = _case()
    _invisible(state)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params).result
    assert not _is_invisible(state)
    assert state[f"{SRD}.entities"]["hero"]["temp_hp"] > 0
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    assert result["result_card"]["title"] == "False Life"


def test_rejected_ordinary_spell_preserves_invisible_and_all_costs():
    state, data, params = _case()
    _invisible(state)
    params["verbal_component_available"] = False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before and _is_invisible(state)


@pytest.mark.parametrize("mode", ["ordinary", "ritual"])
def test_longer_casting_ends_invisible_at_real_accepted_begin_before_completion(mode):
    state, data, params = _case("alarm")
    if mode == "ritual":
        params.update(casting_mode="ritual", slot_resource="ritual", spellbook_readable=True)
    _invisible(state)
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    assert not _is_invisible(state)
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["progress_rounds"] == 0
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 3


def test_actual_slow_failed_longer_begin_ends_ordinary_invisible_source(monkeypatch):
    state, data, params = _case("alarm")
    _external_slow(state, data)
    # A separate caster owns Invisible so the Slow source is not displaced by recasting.
    source = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    source.append(_entity("invisible-mage", [5, 0, 0], participant_ref="invisible-mage"))
    _prepare_invisible(state, "invisible-mage")
    _run(
        "cast_spell_outside_combat",
        state,
        spell="invisibility",
        caster_state="invisible-mage",
        target="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        material_item_ids=[
            m["id"] for m in state[f"{SRD}.entities"]["invisible-mage"]["spell_materials"]
        ],
    )
    monkeypatch.setattr(dice, "_rng", Random(2))
    result = _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params)).result
    assert result["context"]["_slow_cast_failed"] is True
    assert not _is_invisible(state)
    assert state[f"{SRD}.entities"]["hero"].get("extended_casting") is None


@pytest.mark.parametrize("casting", ["cast_spell", "cast_spell_outside_combat"])
@pytest.mark.parametrize("invisible", ["invisibility", "greater_invisibility", "native"])
def test_paid_direct_slow_failure_still_casts_and_ends_only_ordinary_source(
    monkeypatch,
    casting,
    invisible,
):
    state, data, params = _case()
    _external_slow(state, data)
    if invisible == "native":
        state[f"{SRD}.entities"]["hero"]["conditions"].append("invisible")
    else:
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity("invisible-mage", [5, 0, 0], participant_ref="invisible-mage")
        )
        _prepare_invisible(state, "invisible-mage")
        _run(
            "cast_spell_outside_combat",
            state,
            spell=invisible,
            caster_state="invisible-mage",
            target="hero",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            material_item_ids=[
                m["id"] for m in state[f"{SRD}.entities"]["invisible-mage"]["spell_materials"]
            ],
        )
    before = deepcopy(state[f"{SRD}.entities"]["hero"])
    monkeypatch.setattr(dice, "_rng", Random(2))
    result = _run(casting, state, participant_data=data, **params).result["context"]
    assert result["_spell_cast_failed"] is True
    assert _is_invisible(state) is (invisible != "invisibility")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["temp_hp"] == before["temp_hp"]
    assert hero["resources"]["spell_slot_1"] == before["resources"]["spell_slot_1"] - 1


@pytest.mark.parametrize("spell", ["invisibility", "greater_invisibility"])
@pytest.mark.parametrize("amount,source", [(0, "hero"), (1, "hero"), (1, "other"), (1, "")])
def test_effective_source_damage_including_temp_hp_ends_only_ordinary_source(spell, amount, source):
    state, _, _ = _case()
    _invisible(state, spell)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(temp_hp=10, hp=19)
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=amount,
        adjustment=0,
        damage_type="fire",
        source_state_id=source,
    )
    assert guard["hp"] == 19
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 10 - amount
    ended = spell == "invisibility" and amount > 0 and source == "hero"
    assert _is_invisible(state) is not ended


def test_greater_invisibility_survives_actual_new_spell_cast():
    state, data, params = _case()
    _invisible(state, "greater_invisibility")
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert _is_invisible(state)


class Miss:
    def randint(self, low, high):
        assert low == 1
        return 1


def test_later_produce_flame_real_missed_attack_roll_ends_invisible_without_damage(monkeypatch):
    state, data, params = _case("produce_flame")
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _invisible(state)
    before_hp = state[f"{SRD}.entities"]["guard"]["hp"]
    monkeypatch.setattr(dice, "_rng", Miss())
    result = _run(
        "hurl_srd_produce_flame",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        target="guard",
    ).result
    assert result["context"]["_produce_attack_kind"] == "miss"
    assert not _is_invisible(state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before_hp
    assert any(r.get("spell") == "produce_flame" for r in state[f"{SRD}.state"]["turn_effects"])


@pytest.mark.parametrize("spell", ["invisibility", "greater_invisibility"])
def test_actual_composite_damage_consumes_temp_hp_and_ends_only_ordinary_source(spell):
    state, _, _ = _case()
    _invisible(state, spell)
    state[f"{SRD}.entities"]["guard"].update(temp_hp=10, hp=19)
    result = _run(
        "apply_srd_composite_damage",
        state,
        target="guard",
        source_state_id="hero",
        component_1_amount=1,
        component_1_damage_type="fire",
        component_2_amount=1,
        component_2_damage_type="cold",
    ).result
    assert result["context"]["_effective_damage"] == 2
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 8
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 19
    assert _is_invisible(state) is (spell == "greater_invisibility")


def test_removing_cast_source_preserves_independent_native_invisibility():
    state, data, params = _case()
    state[f"{SRD}.entities"]["hero"]["conditions"].append("invisible")
    _invisible(state)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert _is_invisible(state)
    assert not any(r.get("spell") == "invisibility" for r in state[f"{SRD}.state"]["turn_effects"])


def test_binder_preserves_roll_damage_and_native_cards_and_is_idempotent():
    before, _ = _runtime(False)
    after, payload = _runtime()
    assert bind_runtime_consumers(payload.decode()).encode() == payload
    for name in (
        "d20_test",
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "hurl_srd_produce_flame",
    ):
        assert before["tools"][name] == after["tools"][name]


@lru_cache
def _archive():
    with patch.object(
        bundled_packages, "_bundled_srd_root", lambda: PARENT / "rulesets/dnd-srd-5.2.1"
    ):
        primary, reference = bundled_packages._srd_drafts()
    runtime, payload = _runtime()
    record = deepcopy(primary.record)
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": payload}
    for name in ("help-action", "unarmed-strike", "weapon-catalog"):
        path = f"content/{name}.source.json"
        payloads[path] = (PARENT / "rulesets/dnd-srd-5.2.1" / path).read_bytes()
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_actual_archive_invisible_then_paid_cast_reopen_retry(tmp_path, monkeypatch):
    state, data, params = _case()
    _invisible(state)
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "ROOT", PARENT)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: (state, data, params))
    database = tmp_path / "invisible-casting.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "false_life")
        saved = owner.snapshot("casting")
        assert _is_invisible(saved.state)
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        cast, command = _act(reopened, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED
        saved = reopened.snapshot("casting")
        assert not _is_invisible(saved.state)
        assert reopened.execute_action(command) == cast
        assert reopened.snapshot("casting") == saved
