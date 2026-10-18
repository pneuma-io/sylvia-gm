"""NPC spellcasting uses saved stat-block facts through ordinary public MCP actions."""

from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected
from tests.test_srd_public_spell_casting import SRD, _create_spell_campaign
from tests.test_srd_spell_result_cards import _casting_headers


def _act(owner, tool_name, **parameters):
    before = owner.snapshot("casting")
    if tool_name in {"start_group_combat_encounter", "start_combat_encounter_entities"}:
        from tests.srd_fixture_scene import fixture_scene
        parameters.setdefault("combat_scenes", [fixture_scene(before.state[f"{SRD}.entities"], positions={"mage":[0,0,0], "guard":[5,0,0], "hero":[-10,0,0]})])
    command = ActionCommand(
        campaign_id="casting", action_id=f"npc-{before.revision}-{tool_name}",
        idempotency_key=f"npc-{before.revision}-{tool_name}", owner_id="casting-owner",
        writer=WriterRole.DM, expected_revision=before.revision, turn_id="casting-turn",
        package_id=SRD, tool_name=tool_name, parameters=parameters,
    )
    return StatelessMcpMechanics(owner).execute_action(command), command


def _prepare_mage(owner, spells, **changes):
    changes.setdefault("creature_type", "humanoid")
    return _act(
        owner, "prepare_srd_creature", creature_id="mage", name="NPC Mage",
        source="Synthetic stat block: Wisdom +3, spell attack +7, DC 15, CON save +6.",
        armor_class=12, hit_points=25, speed=30, initiative_modifier=20, size_rank=2,
        constitution_save=6, spellcasting_ability="wisdom", spellcasting_modifier=3,
        spell_attack_bonus=7, spell_save_dc=15, spells=spells, **changes,
    )


@pytest.mark.parametrize("spell,uses,choices", [
    ("fire_bolt", 0, {}),
    ("sacred_flame", 0, {}),
    ("cure_wounds", 1, {}),
    ("power_word_kill", 1, {}),
    ("guidance", 0, {"selected_skill": "athletics"}),
    ("mage_armor", 1, {"target":"mage", "target_willing":True}),
    ("aid", 1, {"targets": ["guard", "other"]}),
])
def test_prepared_npc_casts_saved_statistics_and_costs_and_reopens(
    tmp_path, monkeypatch, spell, uses, choices,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "npc.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, spell)
        spell_entry = {"spell": spell, "uses_per_day": uses}
        if spell == "cure_wounds":
            spell_entry["casting_level"] = 3
        npc_supplies = {}
        if _casting_headers()[spell]["material_parts"]:
            hero_state = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
            saved_materials = hero_state["spell_materials"]
            materials = [{
                "id": item["id"], "inventory_item": item["inventory_item"],
                "kind": item["kind"], "components": list(item["components"]),
                "quantity": item["quantity"], "value_cp": item["value_cp"],
            } for item in saved_materials]
            npc_supplies = {
                "inventory": [item["inventory_item"] for item in materials],
                "spell_materials": materials,
            }
        ability_profile = ({"strength_score":10, "dexterity_score":14, "constitution_score":14,
                            "intelligence_score":12, "wisdom_score":16, "charisma_score":10,
                            "proficiency_bonus":4, "equipment":[{"strength":10, "dexterity":14,
                            "unarmored_ac":12, "speed":30, "climb_speed":0, "swim_speed":0,
                            "armor_training":[], "armor":[], "shield":[]}]} if spell == "mage_armor" else {})
        receipt, _ = _prepare_mage(owner, [spell_entry], **ability_profile, **npc_supplies)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert "mage" not in {p.participant_id for p in owner.snapshot("casting").participants}
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters.update(caster_state="mage", **choices)
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        mage = saved.state[f"{SRD}.entities"]["mage"]
        assert mage["resources"] == {}
        assert mage["spell_uses_remaining"][spell] == max(0, uses - 1)
        summary = {r["label"]: r["value"] for r in receipt.result["result_card"]["summary"]}
        assert summary["Caster"] == "NPC Mage"
        assert "Spell slots spent" not in summary
        detail = str(receipt.result["result_card"]["details"])
        if spell == "fire_bolt":
            assert "+ (7)" in detail, detail
            assert len(receipt.result["context"]["_spell_damage"]["results"]) == 1
        elif spell == "sacred_flame":
            assert "DC 15" in detail, detail
        elif spell == "cure_wounds":
            assert saved.state[f"{SRD}.entities"]["guard"]["hp"] > 1
            assert summary["Uses remaining"] == 0
            assert summary["Spell level"] == 3
            assert len(receipt.result["context"]["_spell_healing"]["results"]) == 6
        elif spell == "guidance":
            assert mage["concentration_save_modifier"] == 6
        elif spell == "mage_armor":
            defenses = saved.state[f"{SRD}.state"]["turn_effects"]
            matching = [effect for effect in defenses if effect["kind"] == "timed_actor_defense" and effect["spell"] == "mage_armor" and effect["target"] == "mage"]
            assert len(matching) == 1 and matching[0]["magnitude"] == 15
            assert matching[0]["expires_tick"] > matching[0]["started_tick"]
            assert mage["armor_class"] == 12  # The timed overlay preserves the printed base AC.
        elif spell == "aid":
            assert saved.state[f"{SRD}.entities"]["guard"]["max_hp"] == 105
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        if uses:
            with pytest.raises(MechanicsRejected, match="no uses"):
                _act(owner, "cast_spell_outside_combat", **parameters)
            assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state


@pytest.mark.parametrize("spells", [
    [{"spell": "fire_bolt", "uses_per_day": 0}] * 2,
    [{"spell": "cure_wounds", "uses_per_day": 1, "casting_level": 0}],
])
def test_invalid_npc_spell_profile_does_not_create_a_partial_creature(tmp_path, spells):
    with AuthorityOwner.open(tmp_path / "invalid.db", "casting-owner") as owner:
        _create_spell_campaign(owner, "cure_wounds")
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            _prepare_mage(owner, spells)
        assert owner.snapshot("casting").state == before.state


@pytest.mark.parametrize("waived", [False, True])
def test_npc_components_follow_saved_stat_block_exemptions_after_reopen(tmp_path, waived):
    database = tmp_path / "components.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "fire_bolt")
        receipt, _ = _prepare_mage(
            owner, [{"spell": "fire_bolt", "uses_per_day": 0}],
            spell_component_exemptions=["verbal", "somatic"] if waived else [],
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
    with AuthorityOwner.open(database, "casting-owner") as owner:
        before = owner.snapshot("casting")
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters.update(caster_state="mage", verbal_component_available=False,
                          somatic_component_available=False)
        if waived:
            receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            saved = owner.snapshot("casting")
            assert StatelessMcpMechanics(owner).execute_action(command) == receipt
            assert owner.snapshot("casting").state == saved.state
        else:
            with pytest.raises(MechanicsRejected, match="Verbal component"):
                _act(owner, "cast_spell_outside_combat", **parameters)
            assert owner.snapshot("casting").state == before.state


def test_npc_combat_casts_keep_action_budgets_and_reject_without_spending(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    with AuthorityOwner.open(tmp_path / "combat.db", "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "cure_wounds")
        receipt, _ = _prepare_mage(owner, [
            {"spell": "cure_wounds", "uses_per_day": 1},
            {"spell": "healing_word", "uses_per_day": 1},
            {"spell": "fire_bolt", "uses_per_day": 0},
        ])
        assert receipt.status is ActionStatus.COMMITTED
        receipt, _ = _act(
            owner, "start_group_combat_encounter", combatants=["mage", "hero", "guard"],
        )
        assert receipt.status is ActionStatus.COMMITTED
        receipt, _ = _act(owner, "begin_combat_turn")
        assert receipt.status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        assert before.state[f"{SRD}.combat"]["active_actor"] == "mage"
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters["caster_state"] = "mage"
        # The separate physical-wall case below preserves the actual-cover
        # rejection and atomicity proof. Caller cover cannot override a clear map.
        with pytest.raises(MechanicsRejected, match="saved stat block"):
            _act(owner, "cast_spell", **(parameters | {"slot_resource": "spell_slot_9"}))
        with pytest.raises(MechanicsRejected, match="prepared or listed"):
            _act(owner, "cast_spell", **(parameters | {"spell": "magic_missile"}))
        assert owner.snapshot("casting").state == before.state
        receipt, _ = _act(owner, "cast_spell", **parameters)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_slot_cast_this_turn"] is False
        with pytest.raises(MechanicsRejected, match="action budget"):
            _act(owner, "cast_spell", **(parameters | {"spell": "fire_bolt"}))
        assert owner.snapshot("casting").state == saved.state
        receipt, command = _act(owner, "cast_spell", **(parameters | {"spell": "healing_word"}))
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        mage = saved.state[f"{SRD}.entities"]["mage"]
        assert mage["bonus_action_available"] is False
        assert mage["spell_uses_remaining"] == {"cure_wounds": 0, "healing_word": 0, "fire_bolt": 0}
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state


def test_npc_summon_uses_saved_proficiency_and_materials_after_reopen(tmp_path):
    database = tmp_path / "npc-summon.db"
    part = _casting_headers()["summon_dragon"]["material_parts"][0]
    material = {
        "id": "dragon_material", "inventory_item": "dragon_material_item",
        "kind": "specific", "components": [part["id"]],
        "quantity": part["quantity"], "value_cp": part["minimum_value_cp"],
    }
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "summon_dragon")
        receipt, _ = _prepare_mage(
            owner, [{"spell": "summon_dragon", "uses_per_day": 1, "casting_level": 6}],
            proficiency_bonus=3, inventory=["dragon_material_item"],
            spell_materials=[material],
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters.pop("target")
        parameters.update(
            caster_state="mage", material_item_ids=["dragon_material"],
            summon_id="npc_dragon", summon_distance=30,
            created_actor_placement="Thirty feet east of the mage on clear ground.", created_actor_distance=30,
            created_actor_space_unoccupied=True, created_actor_clear_path=True, created_actor_space_visible=True,
            caster_can_see_summon_space=True, summon_space_unoccupied=True,
            selected_damage_type="fire",
        )
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        dragon = saved.state[f"{SRD}.entities"]["npc_dragon"]
        assert dragon["proficiency_bonus"] == 3
        assert dragon["armor_class"] == 20
        assert dragon["max_hp"] == 60
        assert dragon["summon_attack_bonus"] == 7
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"] == {
            "summon_dragon": 0,
        }
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state


def test_public_death_ward_prevents_npc_instant_death_after_reopen(tmp_path):
    database = tmp_path / "death-ward.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "death_ward")
        receipt, _ = _prepare_mage(
            owner, [{"spell": "power_word_kill", "uses_per_day": 2}],
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        ward, ward_command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert ward.status is ActionStatus.COMMITTED, ward.result
        protected = owner.snapshot("casting")
        assert len(protected.state[f"{SRD}.state"]["turn_effects"]) == 1
        assert StatelessMcpMechanics(owner).execute_action(ward_command) == ward
        assert owner.snapshot("casting").state == protected.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == protected.state
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters.update(spell="power_word_kill", caster_state="mage",
                          caster_can_see_target=True)
        saved_hp = owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"]
        prevented, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert prevented.status is ActionStatus.COMMITTED, prevented.result
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == saved_hp
        assert after.state[f"{SRD}.entities"]["guard"]["is_alive"] is True
        assert after.state[f"{SRD}.state"]["turn_effects"] == []
        assert "Death prevented" in str(prevented.result["result_card"])
        assert StatelessMcpMechanics(owner).execute_action(command) == prevented
        assert owner.snapshot("casting").state == after.state
        killed, _ = _act(owner, "cast_spell_outside_combat", **parameters)
        assert killed.status is ActionStatus.COMMITTED, killed.result
        dead = owner.snapshot("casting")
        assert dead.state[f"{SRD}.entities"]["guard"]["is_alive"] is False
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == dead.state


def test_npc_actual_wall_cover_rejects_atomically_without_spending(tmp_path, monkeypatch):
    from tests.srd_fixture_scene import fixture_scene
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed:None)
    with AuthorityOwner.open(tmp_path / "npc-actual-cover.db", "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "cure_wounds")
        prepared, _ = _prepare_mage(owner, [{"spell":"cure_wounds","uses_per_day":1}])
        assert prepared.status is ActionStatus.COMMITTED
        scene = fixture_scene(owner.snapshot("casting").state[f"{SRD}.entities"],
                              positions={"mage":[0,0,0],"guard":[5,0,0],"hero":[-10,0,0]})
        scene["geometry"] = [{"id":"actual-wall", "map_id":"fixture",
            "volume":{"kind":"box","min":[2,-2,0],"max":[3,2,8]},
            "active":True,"movement":"blocking","visibility":"opaque",
            "destroyed":{"active":False,"movement":"passable","visibility":"clear"},
            "metadata":{"source_ref":"Synthetic solid opaque wall"}}]
        started, _ = _act(owner,"start_group_combat_encounter", combatants=["mage","hero","guard"],combat_scenes=[scene])
        assert started.status is ActionStatus.COMMITTED
        begun, _ = _act(owner,"begin_combat_turn")
        assert begun.status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        assert before.state[f"{SRD}.combat"]["active_actor"] == "mage"
        parameters.pop("caster");parameters.pop("slot_resource")
        parameters.update(caster_state="mage",cover="none",target_distance=0,caster_can_touch_target=True)
        with pytest.raises(MechanicsRejected,match="clear path|Cover|obstruction"):
            _act(owner,"cast_spell",**parameters)
        assert owner.snapshot("casting") == before


def test_npc_pending_touch_healing_needs_no_sight_argument_and_reopens(tmp_path):
    database = tmp_path / 'npc-pending-touch.db'
    with AuthorityOwner.open(database, 'casting-owner') as owner:
        _create_spell_campaign(owner, 'cure_wounds')
        prepared, _ = _prepare_mage(owner, [{'spell':'cure_wounds','uses_per_day':2}])
        assert prepared.status is ActionStatus.COMMITTED
        before = owner.snapshot('casting')
        begun, _ = _act(owner, 'begin_srd_spell_action', spell='cure_wounds',
                        caster_state='mage', target='guard', caster_can_touch_target=True,
                        verbal_component_available=True, somatic_component_available=True)
        assert begun.status is ActionStatus.COMMITTED
        saved = owner.snapshot('casting')
        pending = saved.state[f'{SRD}.state']['pending_srd_action_stack'][-1]
        assert saved.state[f'{SRD}.entities']['guard']['hp'] == before.state[f'{SRD}.entities']['guard']['hp']
    with AuthorityOwner.open(database, 'casting-owner') as owner:
        assert owner.snapshot('casting').state == saved.state
        completed, command = _act(owner, 'complete_srd_pending_action', pending_action_id=pending)
        assert completed.status is ActionStatus.COMMITTED, completed.result
        after = owner.snapshot('casting')
        assert after.state[f'{SRD}.entities']['guard']['hp'] > before.state[f'{SRD}.entities']['guard']['hp']
        assert after.state[f'{SRD}.entities']['mage']['spell_uses_remaining']['cure_wounds'] == 1
        assert after.state[f'{SRD}.state']['pending_srd_action_stack'] == []
        assert owner.execute_action(command) == completed and owner.snapshot('casting').state == after.state
    with AuthorityOwner.open(database, 'casting-owner') as owner:
        assert owner.snapshot('casting').state == after.state
