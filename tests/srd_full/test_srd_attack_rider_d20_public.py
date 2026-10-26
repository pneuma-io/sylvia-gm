"""Paid live riders affect real native rolls and keep saved public retries inert."""

from functools import lru_cache
from random import Random

import pytest
from scripts.srd_full.srd_attack_rider_d20 import bind_runtime_consumers
from scripts.srd_full.srd_parameter_declarations import compact_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from tests import test_srd_attack_hit_riders as hits
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_reaction_windows import _parameters
from tests.srd_full.test_srd_spell_damage_riders import _case

from sylvia_gm import dice
from sylvia_gm.agent._provider_session import _derive_mechanics_parameters
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus

SRD = "wotc.srd.5.2.1"
_original_runtime = hits._runtime


@lru_cache
def _runtime():
    _, encoded = _original_runtime()
    text = bind_runtime_consumers(encoded.decode())
    return _load_runtime(text), compact_runtime(text).encode()


class NativeDraws(Random):
    """Observe genuine seeded native dice without choosing their outcomes."""

    def __init__(self, seed):
        super().__init__(seed)
        self.draws = []

    def randint(self, low, high):
        value = super().randint(low, high)
        self.draws.append((high, value))
        return value


def _case_for(spell):
    if spell == "shining_smite":
        return hits._hit_case(spell, 2)
    state, data, params = _case(spell)
    if spell == "hex":
        params.update(target="hero", target_distance=0, rider_ability="dexterity")
        state[f"{SRD}.state"].pop("spatial_scene")
        # This campaign fixture grants Intelligence/Wisdom save proficiency, not Dexterity.
        state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = {
            "dexterity": data["abilities"]["dexterity"] // 2 - 5,
        }
    return state, data, params


def _weapon_parameters():
    parameters = _parameters() | {hits.INTENT: False}
    parameters.pop("actor_state")
    return parameters


def _check_parameters(owner, name, **choices):
    """Use the same participant derivation as the real provider and existing check tests."""
    tool = next(
        tool for tool in StatelessMcpMechanics(owner).list_tools("casting") if tool.name == name
    )
    return _derive_mechanics_parameters(
        choices, tool.parameter_derivations or {}, owner.snapshot("casting").participants
    )


@pytest.mark.parametrize("spell", ["hex", "hunters_mark", "shining_smite"])
def test_paid_public_rider_native_roll_reopens_and_retries_without_cost_or_rng_repeat(
    tmp_path, monkeypatch, spell
):
    monkeypatch.setattr(hits, "_runtime", _runtime)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(public, "bundled_srd_archive", hits._archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case_for(spell))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", NativeDraws(5))
    database = tmp_path / f"{spell}.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, spell, target_overrides={"hp": 100, "max_hp": 100, "temp_hp": 0}
        )
        combatants = ["hero", "guard", "other"]
        if spell == "hex":
            prepared, _ = _prepare_mage(owner, [{"spell": "sacred_flame", "uses_per_day": 1}])
            assert prepared.status is ActionStatus.COMMITTED
            combatants.append("mage")
        _act(owner, "start_group_combat_encounter", combatants=combatants)
        _act(owner, "begin_combat_turn")
        before = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        slot = "spell_slot_2" if spell == "shining_smite" else "spell_slot_1"
        if spell == "shining_smite":
            dice._rng = NativeDraws(5)
            begun, _ = _act(
                owner,
                "begin_srd_character_attack",
                **(_weapon_parameters() | {hits.INTENT: True}),
            )
            original_id = begun.result["context"]["_pending_action_id"]
            _act(owner, "complete_srd_pending_action", pending_action_id=original_id)
            params.pop("rider_ability")
            child, _ = _act(
                owner, hits.BEGIN, **(params | {"rider_pending_action_id": original_id})
            )
            _act(
                owner,
                "complete_srd_pending_action",
                pending_action_id=child.result["context"]["_pending_action_id"],
            )
            _act(owner, "complete_srd_pending_action", pending_action_id=original_id)
        else:
            paid, _ = _act(owner, "cast_spell", **params)
            assert paid.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        caster = saved.state[f"{SRD}.entities"]["hero"]
        assert caster["resources"][slot] == before["resources"][slot] - 1
        assert caster["bonus_action_available"] is False
        sources = saved.state[f"{SRD}.state"]["attack_rider_sources"]
        assert any(row["spell"] == spell and row["installed"] for row in sources.values())
        if spell == "hex":
            _act(owner, "end_combat_encounter", end_reason="fighting_stopped")
            saved = owner.snapshot("casting")

    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        dice._rng = NativeDraws(8)
        if spell == "hex":
            receipt, command = _act(
                owner,
                "ability_check",
                **_check_parameters(
                    owner,
                    "ability_check",
                    actor="hero",
                    ability="dexterity",
                    skill="acrobatics",
                    circumstantial_modifier=0,
                    target_number=10,
                    advantage=False,
                    disadvantage=False,
                ),
            )
            context = receipt.result["context"]
            assert context["_rider_check_disadvantage"] is True
            assert context["_effective_disadvantage"] is True
            assert context["_d20_test"]["total"] == min(v for _, v in dice._rng.draws)
        elif spell == "hunters_mark":
            receipt, command = _act(
                owner,
                "search_srd_action",
                **_check_parameters(
                    owner,
                    "search_srd_action",
                    actor="hero",
                    skill="perception",
                    target_number=10,
                    circumstantial_modifier=0,
                    advantage=False,
                    disadvantage=False,
                    finding_target="guard",
                ),
            )
            context = receipt.result["context"]
            assert context["_rider_finding_advantage"] is True
            assert context["_d20_test"]["total"] == max(v for _, v in dice._rng.draws)
        else:
            receipt, command = _act(owner, "begin_srd_character_attack", **_weapon_parameters())
            event_id = receipt.result["context"]["_pending_action_id"]
            event = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_actions"][event_id]
            assert event["attack_roll"]["total"] == max(v for _, v in dice._rng.draws)
        assert receipt.status is ActionStatus.COMMITTED
        assert [sides for sides, _ in dice._rng.draws] == [20, 20]
        assert all(1 <= value <= 20 for _, value in dice._rng.draws)
        rolled = owner.snapshot("casting")
        draws = list(dice._rng.draws)
        assert owner.execute_action(command) == receipt
        assert dice._rng.draws == draws and owner.snapshot("casting") == rolled

    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == rolled
        assert owner.execute_action(command) == receipt
        assert dice._rng.draws == draws
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"][slot]
            == caster["resources"][slot]
        )
        if spell == "hex":
            _, _, save_parameters = cards._spell_case("sacred_flame")
            save_parameters.pop("caster")
            save_parameters.pop("slot_resource")
            save_parameters.update(caster_state="mage", target="hero", target_distance=10)
            dice._rng = NativeDraws(5)
            save, save_command = _act(owner, "cast_spell_outside_combat", **save_parameters)
            assert save.status is ActionStatus.COMMITTED
            assert len(save.result["context"]["_spell_save"]["results"]) == 1
            assert save.result["context"]["_spell_save"]["results"] == [20]
            assert [sides for sides, _ in dice._rng.draws].count(20) == 1
            after_save = owner.snapshot("casting")
            assert (
                after_save.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["sacred_flame"]
                == 0
            )
            save_draws = list(dice._rng.draws)
            assert owner.execute_action(save_command) == save
            assert owner.snapshot("casting") == after_save
            assert dice._rng.draws == save_draws
        elif spell == "shining_smite":
            count = len(dice._rng.draws)
            _act(owner, "complete_srd_pending_action", pending_action_id=event_id)
            assert all(sides != 20 for sides, _ in dice._rng.draws[count:])
