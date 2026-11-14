"""A paid shared projection retains independent receiving bodies and exact returns."""

import os
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from scripts.srd_full.srd_astral_projection import bind_completed_returns, bind_runtime_consumers
from scripts.srd_full.srd_parameter_declarations import compact_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_astral_projection as original
from tests import test_srd_public_spell_casting as public
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity


class MaximumDice:
    def randint(self, _a, b):
        return b


@pytest.fixture(autouse=True)
def owners(monkeypatch):
    # The original fixture owns native mechanic compilation and strict archive admission.
    original._runtime.cache_clear()
    path = os.environ.get("FC14_ASTRAL_NATIVE_PARENT")
    encoded = Path(path).read_bytes() if path else original.source_runtime()[1]
    if path:
        assert sha256(encoded).hexdigest() == (
            "fe066d1251fb874d6cb62f0fcb8eb925d4db40d56df0a3c8a8831158ce2624bc"
        )
    parsed = _load_runtime(encoded.decode())
    monkeypatch.setattr(original, "source_runtime", lambda: (deepcopy(parsed), encoded))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", MaximumDice())
    from tests import test_srd_action_budget as budget
    from tests import test_srd_spell_result_cards as cards

    monkeypatch.setattr(budget, "_mechanic", original._mechanic)
    monkeypatch.setattr(
        cards, "_spell_definitions", lambda: original._runtime()[0]["definitions"]["spells"]
    )
    execute = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _case(companion="ally"):
    state, data, params = original._case()
    for lot in state[f"{SRD}.entities"]["hero"]["spell_materials"]:
        lot["quantity"] = 2
    scene = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    scene += [
        _entity(
            "ally-body",
            [5, 0, 0],
            participant_ref="ally",
            creature_type="humanoid",
            plane="material",
        ),
        _entity(
            "physical-mage",
            [10, 10, 0],
            participant_ref="physical-mage",
            creature_type="humanoid",
            plane="material",
        ),
        _entity(
            "astral-mage",
            [100, 110, 0],
            participant_ref="astral-mage",
            creature_type="humanoid",
            plane="astral",
        ),
    ]
    params.update(astral_companions=[companion], all_targets_willing=True)
    return state, data, params


def _prepare(owner, identity, spells=()):
    result, _ = _act(
        owner,
        "prepare_srd_creature",
        creature_id=identity,
        name=identity,
        source="Synthetic prepared humanoid: HP30, AC10, CON+0, Wisdom+3, spell DC15.",
        armor_class=10,
        hit_points=30,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=0,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=list(spells),
    )
    assert result.status is ActionStatus.COMMITTED, result.result


def test_paid_companions_independent_native_damage_return_reopen_and_retry(tmp_path, monkeypatch):
    # Add the real completion suffix to this original owner's native runtime/archive seam.
    base = original._runtime
    raw, encoded = base()
    text = bind_completed_returns(
        encoded.decode(), {"cast_spell", "cast_spell_outside_combat", "complete_srd_pending_action"}
    )
    assert (
        bind_completed_returns(
            text, {"cast_spell", "cast_spell_outside_combat", "complete_srd_pending_action"}
        )
        == text
    )
    # The original constructor already checks byte repeat before shared declarations;
    # after encoding, resolution preserves the same graph and all schemas.
    assert _load_runtime(bind_runtime_consumers(text)) == _load_runtime(text)
    current = _load_runtime(text), compact_runtime(text).encode()
    monkeypatch.setattr(original, "_runtime", lambda: current)
    original._mechanic.cache_clear()
    original._archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", original._archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case())
    database = tmp_path / "astral-companions.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "astral_projection")
        _prepare(owner, "ally")
        _prepare(owner, "physical-mage", [{"spell": "magic_missile", "uses_per_day": 1}])
        _prepare(owner, "astral-mage", [{"spell": "magic_missile", "uses_per_day": 2}])
        before = owner.snapshot("casting")
        begin, begin_input = _act(owner, "begin_srd_spell_cast", **original._begin_values(params))
        assert begin.status is ActionStatus.COMMITTED, begin.result
        assert owner.execute_action(begin_input) == begin
        extent = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["extended_casting"]
        assert extent["material_target_count"] == 2 and extent["astral_companions"] == ["ally"]
        progress, progress_input = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progress.status is ActionStatus.COMMITTED, progress.result
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        cast, cast_input = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        projected = owner.snapshot("casting")
        entities = projected.state[f"{SRD}.entities"]
        sources = projected.state[f"{SRD}.state"]["astral_projection_sources"]
        caster_id = entities["hero"]["astral_projection_source_id"]
        ally_id = entities["ally"]["astral_projection_source_id"]
        body_id = sources[ally_id]["body_id"]
        assert sources[ally_id]["cast_id"] == sources[caster_id]["cast_id"]
        assert sources[ally_id]["source_caster_id"] == "hero"
        assert len(sources) == 2 and [
            r["quantity"] for r in entities["hero"]["spell_materials"]
        ] == [0, 0]
        assert (
            entities["hero"]["resources"]["spell_slot_9"]
            == before.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] - 1
        )
        assert entities[body_id]["conditions"] == ["unconscious"] and entities["ally"]["hp"] == 30
        attack_params = {k: v for k, v in params.items() if k not in {"caster", "slot_resource"}}
        attack_params.update(
            spell="magic_missile",
            caster_state="astral-mage",
            target="ally",
            material_item_ids=[],
            target_distance=10,
        )
        first, first_input = _act(owner, "cast_spell_outside_combat", **attack_params)
        assert first.status is ActionStatus.COMMITTED, first.result
        entities = owner.snapshot("casting").state[f"{SRD}.entities"]
        assert entities["ally"]["hp"] == 15 and entities[body_id]["hp"] == 30
        physical, physical_input = _act(
            owner,
            "cast_spell_outside_combat",
            **(attack_params | {"caster_state": "physical-mage", "target": body_id}),
        )
        assert physical.status is ActionStatus.COMMITTED, physical.result
        entities = owner.snapshot("casting").state[f"{SRD}.entities"]
        assert entities[body_id]["hp"] == 15 and entities["ally"]["hp"] == 15
        received = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == received
        last, last_input = _act(owner, "cast_spell_outside_combat", **attack_params)
        assert last.status is ActionStatus.COMMITTED, last.result
        returned = owner.snapshot("casting")
        entities = returned.state[f"{SRD}.entities"]
        sources = returned.state[f"{SRD}.state"]["astral_projection_sources"]
        assert entities["ally"]["hp"] == 15 and entities["ally"]["astral_projection_role"] is None
        assert sources[ally_id]["installed"] is False and sources[caster_id]["installed"] is True
        assert entities["astral-mage"]["spell_uses_remaining"]["magic_missile"] == 0
        dismiss, dismiss_input = _act(owner, "dismiss_srd_astral_projection", caster_state="hero")
        assert dismiss.status is ActionStatus.COMMITTED, dismiss.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["hero"]["hp"] == 40
        assert not final.state[f"{SRD}.state"]["astral_projection_sources"][caster_id]["installed"]
        for command, receipt in [
            (last_input, last),
            (physical_input, physical),
            (first_input, first),
            (cast_input, cast),
            (progress_input, progress),
            (dismiss_input, dismiss),
        ]:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == final


def test_range_and_willingness_reject_before_long_cast_costs():
    state, data, params = _case("guard")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ten feet"):
        _run("begin_srd_spell_cast", state, participant_data=data, **original._begin_values(params))
    assert state == before
    params["all_targets_willing"] = False
    with pytest.raises(RulesetPreconditionRejected, match="willing companions"):
        _run("begin_srd_spell_cast", state, participant_data=data, **original._begin_values(params))
    assert state == before
