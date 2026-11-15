"""Actual tremor cast identities reach paid Dispel and exact dismissal across reopen/retry."""

from copy import deepcopy
from random import Random

import pytest
import yaml
from scripts import srd_earthquake_sources as sources
from scripts.srd_full.srd_runtime_defaults import restore_tool_defaults

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_earthquake as quake
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity

SRD = quake.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from quake._compiled.__wrapped__(monkeypatch)


def _case():
    state, data, params = quake._case()
    # Both spells are genuine initial character choices, validated at their actual casts.
    data["spellcasting_grants"][0]["spells"].append("dispel_magic")
    quake._target(state)["prepared_spells"].append("dispel_magic")
    return state, data, params


def _dispel(source_id, *, level=3, point=None):
    return {
        "spell": "dispel_magic",
        "caster": "hero",
        "caster_state": "hero",
        "slot_resource": f"spell_slot_{level}",
        "recovery_choice": "earthquake",
        "recovery_source_id": source_id,
        "recovery_position": point or [75, 0, 0],
        "verbal_component_available": True,
        "somatic_component_available": True,
    }


def _second_cast(state, data, params):
    supplies = deepcopy(quake._target(state)["spell_materials"])
    quake._run(
        "prepare_srd_creature",
        state,
        data,
        creature_id="second-caster",
        name="Second caster",
        source="Admitted native caster: Earthquake1/day level8 DC18; fractured rock.",
        armor_class=12,
        hit_points=40,
        speed=30,
        size_rank=2,
        creature_type="humanoid",
        initiative_modifier=0,
        dexterity_save=0,
        constitution_save=2,
        spellcasting_ability="wisdom",
        spellcasting_modifier=4,
        spell_attack_bonus=10,
        spell_save_dc=18,
        spells=[{"spell": "earthquake", "uses_per_day": 1, "casting_level": 8}],
        inventory=[s["inventory_item"] for s in supplies],
        spell_materials=supplies,
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("second-caster", [0, -40, 0], participant_ref="second-caster")
    )
    choices = {k: v for k, v in params.items() if k not in {"caster", "slot_resource"}}
    quake._cast(state, data, choices | {"caster_state": "second-caster"})
    return deepcopy(quake._source(state))


def test_genuine_allocator_identity_dismisses_one_cast_and_preserves_prone_and_overlap():
    state, data, params = _case()
    quake._cast(state, data, params)
    first = deepcopy(quake._source(state))
    hero = quake._target(state)
    assert hero["concentration_cast_id"] == first["cast_id"]
    assert state[f"{SRD}.state"]["spell_source_serial"] == 1
    second = _second_cast(state, data, params)
    assert first["cast_id"] != second["cast_id"]
    before = deepcopy(hero["resources"])
    result = quake._run(
        "dismiss_srd_spell", state, data, caster_state="hero", cast_id=first["cast_id"]
    )
    assert result.result["context"]["_dismiss_count"] == 1
    pool = state[f"{SRD}.state"]["persistent_spell_areas"]
    assert pool[first["id"]]["active"] is False
    assert pool[second["id"]]["active"] is True
    assert quake._target(state)["resources"] == before
    assert quake._target(state)["concentration_spell_id"] is None
    assert "prone" in quake._target(state, "victim")["conditions"]
    assert quake._cost(state, data, [140, 0, 0], [150, 0, 0]) == pytest.approx(20)
    snapshot = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        quake._run("dismiss_srd_spell", state, data, caster_state="hero", cast_id=second["cast_id"])
    assert state == snapshot


def test_paid_failed_check_preserves_source_then_actual_slot_eight_ends_exact_tremor(monkeypatch):
    state, data, params = _case()
    quake._cast(state, data, params)
    source = deepcopy(quake._source(state))
    slots = deepcopy(quake._target(state)["resources"])
    monkeypatch.setattr(dice, "_rng", Random(1))
    failed = quake._cast(state, data, _dispel(source["id"]))
    context = failed.result["context"]
    assert context["_spell_summary_rows"][0]["value"] is False
    detail = context["_spell_detail_rows"][1]["value"]
    assert "d20 5;" in detail and "DC 18" in detail
    assert quake._source(state)["active"] is True
    assert quake._target(state)["resources"]["spell_slot_3"] == slots["spell_slot_3"] - 1
    ended = quake._cast(state, data, _dispel(source["id"], level=8))
    context = ended.result["context"]
    assert context["_spell_summary_rows"][0]["value"] is True
    assert context["_spell_detail_rows"][1]["value"] == "Automatic at this slot level"
    assert quake._source(state)["active"] is False
    assert quake._target(state)["concentration_spell_id"] is None
    assert "prone" in quake._target(state, "victim")["conditions"]
    assert quake._cost(state, data, [140, 0, 0], [150, 0, 0]) == pytest.approx(10)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        quake._cast(state, data, _dispel(source["id"], level=8))
    assert state == before


@pytest.mark.parametrize("point", [[0, 0, 0], [75, 0, 5], [250, 0, 0]])
def test_actual_disk_and_range_reject_illegal_paid_selection_atomically(point):
    state, data, params = _case()
    quake._cast(state, data, params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        quake._cast(state, data, _dispel(quake._source(state)["id"], point=point))
    assert state == before


def test_source_binder_preserves_other_actual_owners_and_is_repeat_stable():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    before = yaml.load(text, Loader=yaml.CSafeLoader)
    after = yaml.load(sources.bind_runtime(text), Loader=yaml.CSafeLoader)
    restore_tool_defaults(before)
    restore_tool_defaults(after)
    assert before == after


def test_public_paid_tremor_failed_dispel_reopens_exact_dismissal_retries(tmp_path, monkeypatch):
    quake._public_fixture(monkeypatch)
    monkeypatch.setattr(jar.public_casting, "_spell_case", lambda *_a, **_k: _case())
    database = tmp_path / "earthquake-source.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "earthquake")
        initial = owner.snapshot("casting")
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = deepcopy(quake._source(saved.state))
        assert source["cast_id"] == quake._target(saved.state)["concentration_cast_id"]
        assert quake._target(saved.state)["resources"]["spell_slot_8"] == (
            quake._target(initial.state)["resources"]["spell_slot_8"] - 1
        )
        assert owner.execute_action(cast_command) == cast
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        monkeypatch.setattr(dice, "_rng", Random(1))
        failed, dispel_command = _act(owner, "cast_spell_outside_combat", **_dispel(source["id"]))
        assert failed.status is ActionStatus.COMMITTED, failed.result
        retained = owner.snapshot("casting")
        assert quake._source(retained.state)["active"] is True
        assert quake._target(retained.state)["resources"]["spell_slot_3"] == (
            quake._target(saved.state)["resources"]["spell_slot_3"] - 1
        )
        assert owner.execute_action(dispel_command) == failed
        dismissed, command = _act(
            owner, "dismiss_srd_spell", caster_state="hero", cast_id=source["cast_id"]
        )
        assert dismissed.status is ActionStatus.COMMITTED, dismissed.result
        final = owner.snapshot("casting")
        assert quake._source(final.state)["active"] is False
        assert "prone" in quake._target(final.state, "victim")["conditions"]
        assert owner.execute_action(command) == dismissed and owner.snapshot("casting") == final
        assert owner.execute_action(cast_command) == cast and owner.snapshot("casting") == final
        assert owner.execute_action(dispel_command) == failed and owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
