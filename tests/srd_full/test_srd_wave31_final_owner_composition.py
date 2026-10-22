"""Two paid Wave31 compositions consume final chronological and occupied-body owners."""

from copy import deepcopy
from dataclasses import replace
from math import ceil
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_courier_chronology as chronology
from tests import test_srd_magic_jar as jar
from tests import test_srd_magic_jar_healing as healing
from tests import test_srd_magic_jar_materials as materials
from tests import test_srd_mirage_fall_composition as actual
from tests import test_srd_public_spell_casting as public
from tests.srd_full.test_srd_magic_jar_circle import _npc

actual_owners = actual.actual_owners
SRD = actual.SRD


def _commit(owner, tool_name, **parameters):
    receipt, command = actual._act(owner, tool_name, **parameters)
    assert receipt.status is ActionStatus.COMMITTED, receipt.result
    return receipt, command


def test_paid_final_chronology_crossing_aftermath_expiry_reopen_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(chronology, "_runtime", lambda: (actual.RUNTIME, actual.ENCODED))
    monkeypatch.setattr(public, "_spell_case", chronology._public_case)
    database = tmp_path / "final-courier.db"
    commands = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "animal_messenger", target_overrides={"hp": 500, "max_hp": 500}
        )
        monkeypatch.setattr(dice, "_rng", Random(2))
        commands.append(_commit(owner, "cast_spell_outside_combat", **params))
        _commit(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        haste = chronology.body._gas_casting(params) | {
            "spell": "haste",
            "caster_state": "guard",
            "target": "guard",
        }
        commands.append(_commit(owner, "cast_spell_outside_combat", **haste))
        hazard = chronology.body._gas_casting(params) | {
            "spell": "spirit_guardians",
            "caster_state": "hazard_caster",
            "environment_unaffected_targets": [],
            "environment_spirit_appearance": "angelic",
        }
        commands.append(_commit(owner, "cast_spell_outside_combat", **hazard))
        saved = owner.snapshot("casting")
        actors = saved.state[f"{SRD}.entities"]
        assert actors["hero"]["resources"]["spell_slot_2"] == 2
        assert actors["guard"]["spell_uses_remaining"]["haste"] == 0
        assert actors["hazard_caster"]["spell_uses_remaining"]["spirit_guardians"] == 0
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for receipt, command in commands:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
        monkeypatch.setattr(dice, "_rng", Random(2))
        crossed, crossing = _commit(owner, "advance_srd_lasting_time", elapsed_rounds=10)
        saved = owner.snapshot("casting")
        actor = saved.state[f"{SRD}.entities"]["guard"]
        assert actor["hp"] == 490
        assert actor["last_spirit_guardians_save"]["turn"] == pytest.approx(20.4)
        assert actor["haste_lethargy_until_tick"] == pytest.approx(22.4)
        assert actor["spell_haste"] is False
        assert actor.get("concentration_spell_id") is None
        assert list(chronology.courier._position(saved.state)) == pytest.approx([113.5, 0, 0])
        assert saved.state[f"{SRD}.state"]["lasting_tick"] == 22
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(crossing) == crossed
        assert owner.snapshot("casting") == saved
        continued, continuation = _commit(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        after = owner.snapshot("casting")
        assert list(chronology.courier._position(after.state)) == pytest.approx(
            [117.1666666666667, 0, 0]
        )
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == 490
        clock = after.state[f"{SRD}.state"]["lasting_tick"]
        area = after.state[f"{SRD}.state"]["persistent_spell_areas"]["hazard_caster"]
        expires = area["expires_tick"]
        assert area["active"] is True and expires > clock
        expired, expiry = _commit(
            owner, "advance_srd_lasting_time", elapsed_rounds=ceil((expires - clock) / 2)
        )
        final = owner.snapshot("casting")
        assert (
            final.state[f"{SRD}.state"]["persistent_spell_areas"]["hazard_caster"]["active"]
            is False
        )
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == 490
        assert (
            final.state[f"{SRD}.entities"]["guard"]["animal_messenger"]["last_tick"]
            == final.state[f"{SRD}.state"]["lasting_tick"]
        )
        commands += [(crossed, crossing), (continued, continuation), (expired, expiry)]
        for receipt, command in commands:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert owner.execute_action(expiry) == expired
        assert owner.snapshot("casting") == final


def _jar_case():
    state, data, params = materials._case()
    state[f"{SRD}.entities"]["guard"]["temp_hp"] = 9
    scene = state[f"{SRD}.state"]["spatial_scene"]
    healer = deepcopy(next(row for row in scene["entities"] if row["id"] == "guard"))
    healer.update(id="jar-healer", position=[3, -2, 0])
    healer["metadata"]["participant_ref"] = "jar-healer"
    scene["entities"].append(healer)
    return state, data, params


def test_paid_final_jar_body_materials_healing_flags_return_reopen_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public, "_spell_case", lambda *_args, **_kwargs: _jar_case())
    create = AuthorityOwner.create_campaign

    def add_host(owner, command):
        hero = command.participants[0]
        host = replace(hero, participant_id="guard", data=jar._host_artifact(hero.data))
        return create(owner, replace(command, participants=(*command.participants, host)))

    monkeypatch.setattr(AuthorityOwner, "create_campaign", add_host)
    database = tmp_path / "final-jar.db"
    commands = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "magic_jar", target_overrides={"hp": 60, "max_hp": 60, "temp_hp": 9}
        )
        commands.append(_commit(owner, "begin_srd_spell_cast", **jar._begin(params)))
        commands.append(
            _commit(
                owner,
                "continue_srd_spell_cast",
                caster_state="hero",
                elapsed_rounds=10,
                uninterrupted=True,
                verbal_component_available=True,
                somatic_component_available=True,
                material_component_available=True,
            )
        )
        commands.append(_commit(owner, "cast_spell_outside_combat", **params))
        commands.append(
            _commit(
                owner,
                "project_srd_magic_jar_soul",
                caster_state="hero",
                host_state="guard",
                host_character="guard",
            )
        )
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="[Mm]aterial"):
            actual._act(
                owner, "cast_spell_outside_combat", **materials._stone(params, "remote-dust")
            )
        assert owner.snapshot("casting") == before
        commands.append(_commit(owner, "cast_spell_outside_combat", **materials._stone(params)))
        stock = owner.snapshot("casting")
        entities = stock.state[f"{SRD}.entities"]
        assert entities["hero"]["resources"]["spell_slot_4"] == 2
        assert entities["guard"]["spell_materials"][0]["quantity"] == 1
        assert (
            next(row for row in entities["hero"]["spell_materials"] if row["id"] == "remote-dust")[
                "quantity"
            ]
            == 2
        )
        npc = _npc("jar-healer", "cure_wounds", "humanoid")
        npc["spells"] += [
            {"spell": spell, "uses_per_day": 3} for spell in ("magic_missile", "beacon_of_hope")
        ]
        commands.append(_commit(owner, "prepare_srd_creature", **npc))
        missile = healing._cure(params) | {
            "spell": "magic_missile",
            "projectile_targets": ["guard"] * 3,
        }
        commands.append(_commit(owner, "cast_spell_outside_combat", **missile))
        commands.append(
            _commit(
                owner,
                "cast_spell_outside_combat",
                **(healing._cure(params) | {"spell": "beacon_of_hope", "targets": ["guard"]}),
            )
        )
        saved = owner.snapshot("casting")
        entities = saved.state[f"{SRD}.entities"]
        assert entities["guard"]["spell_beacon_of_hope"] is True
        assert entities["hero"].get("spell_beacon_of_hope") is not True
        hp = entities["hero"]["hp"]
        assert hp == entities["guard"]["hp"] and hp < 60
        temp = entities["guard"]["temp_hp"]
        sources = deepcopy(saved.state[f"{SRD}.state"]["turn_effects"])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        monkeypatch.setattr(dice, "_rng", Random(1))
        amounts = []
        execute = _effect_executor._execute_effect

        def observe_healing(effect, context, executor=None):
            result = execute(effect, context, executor)
            if (
                context.current_tool == "restore_srd_magic_jar_body_hit_points"
                and effect.get("set", {}).get("field") == "_beacon_healing_amount"
            ):
                amounts.append(context.resolve_field("_beacon_healing_amount"))
            return result

        monkeypatch.setattr(_effect_executor, "_execute_effect", observe_healing)
        healed, healing_command = _commit(
            owner, "cast_spell_outside_combat", **healing._cure(params)
        )
        commands.append((healed, healing_command))
        assert amounts == [20]
        after = owner.snapshot("casting")
        entities = after.state[f"{SRD}.entities"]
        assert entities["hero"]["hp"] == entities["guard"]["hp"] == min(60, hp + 20)
        assert entities["guard"]["temp_hp"] == temp
        assert "incapacitated" in entities["guard"]["conditions"]
        assert entities["guard"]["spell_beacon_of_hope"] is True
        assert after.state[f"{SRD}.state"]["turn_effects"] == sources
        assert entities["jar-healer"]["spell_uses_remaining"]["cure_wounds"] == 2
        for receipt, command in commands:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == after
        for destination in ("container", "body"):
            commands.append(
                _commit(
                    owner, "return_srd_magic_jar_soul", caster_state="hero", destination=destination
                )
            )
        final = owner.snapshot("casting")
        entities = final.state[f"{SRD}.entities"]
        assert entities["hero"]["hp"] == 40
        assert entities["guard"]["hp"] == min(60, hp + 20)
        assert entities["guard"]["temp_hp"] == temp
        assert entities["guard"]["spell_beacon_of_hope"] is True
        assert entities["guard"]["spell_materials"][0]["quantity"] == 1
        assert entities["hero"]["inventory"] == before.state[f"{SRD}.entities"]["hero"]["inventory"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        for receipt, command in commands:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == final
