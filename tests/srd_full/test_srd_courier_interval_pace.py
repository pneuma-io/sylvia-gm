"""Authentic movement sources split courier travel without changing query clocks."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_courier_body_movement import bind_runtime_consumers as bind_body
from scripts.srd_full.srd_courier_interval_pace import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_courier_body_movement as body
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_wave7_courier as courier
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = bind_runtime_consumers(bind_body(source.read_text(encoding="utf-8")))
    return _load_runtime(text), text.encode()


@lru_cache
def _mechanic(name):
    runtime, content = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=content,
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
def _authored(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])


def _npc(spell, identity="pace_caster"):
    result = body._gas_preparation()
    result.update(
        creature_id=identity,
        name="Source pace caster",
        spells=[{"spell": spell, "uses_per_day": 1}],
    )
    return result


def _cast(state, params, spell, identity="pace_caster", extra_spells=()):
    preparation = _npc(spell, identity)
    preparation["spells"] += [{"spell": extra, "uses_per_day": 1} for extra in extra_spells]
    _run("prepare_srd_creature", state, **preparation)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(identity, [20, 0, 0], participant_ref=identity)
    )
    casting = body._gas_casting(params)
    casting.update(spell=spell, caster_state=identity)
    if spell == "longstrider":
        casting["targets"] = ["guard"]
    _run("cast_spell_outside_combat", state, **casting)
    assert state[f"{SRD}.entities"][identity]["spell_uses_remaining"][spell] == 0


def _query(state, tick, end):
    before = deepcopy(state)
    context = _run(
        "project_srd_animal_messenger_pace",
        state,
        target_state="guard",
        at_tick=tick,
        interval_end=end,
    ).result["context"]
    assert state == before
    return context


def test_paid_slow_expiry_uses_actual_flying_body_then_unreduced_pace():
    state, _, params = courier._courier(destination=(10000, 0, 0), flying_speed=60)
    _cast(state, params, "slow")
    assert _query(state, 0, 30)["_courier_next_pace_tick"] == 20
    assert _query(state, 20, 30)["_courier_source_slow"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=15)
    assert courier._position(state) == pytest.approx([20 + 264000 * (20 / 2 + 10) / 28800, 0, 0])
    assert state[f"{SRD}.entities"]["guard"]["animal_messenger"]["last_tick"] == 30


def test_paid_haste_expiry_blocks_only_its_real_two_tick_aftermath():
    state, _, params = courier._courier(destination=(10000, 0, 0), exhaustion_level=2)
    _cast(state, params, "haste")
    assert _query(state, 0, 30)["_courier_pace"] == pytest.approx(132000 / 28800)
    result = _query(state, 20, 30)
    assert result["_courier_pace"] == 0 and result["_courier_next_pace_tick"] == 22
    assert _query(state, 22, 30)["_courier_pace"] == pytest.approx(132000 / 28800 * 2 / 3)
    _run("advance_srd_lasting_time", state, elapsed_rounds=15)
    assert courier._position(state) == pytest.approx([20 + 132000 * (20 + 8 * 2 / 3) / 28800, 0, 0])


def test_source_replacement_does_not_forge_old_haste_aftermath():
    state, _, params = courier._courier(destination=(10000, 0, 0), flying_speed=60)
    _cast(state, params, "haste", extra_spells=("slow",))
    casting = body._gas_casting(params)
    casting.update(spell="slow", caster_state="pace_caster")
    _run("cast_spell_outside_combat", state, **casting)
    result = _query(state, 20, 24)
    assert result["_courier_source_lethargy"] is False


def test_paid_longstrider_expiry_uses_current_equipment_base_without_stale_bonus():
    state, _, params = courier._courier(destination=(20000, 0, 0), exhaustion_level=2)
    _cast(state, params, "longstrider")
    assert _query(state, 0, 1210)["_courier_pace"] == pytest.approx(132000 / 28800 * 30 / 40)
    assert _query(state, 1200, 1210)["_courier_pace"] == pytest.approx(132000 / 28800 * 20 / 30)
    _run("advance_srd_lasting_time", state, elapsed_rounds=605)
    assert courier._position(state) == pytest.approx(
        [20 + 132000 * (1200 * 30 / 40 + 10 * 20 / 30) / 28800, 0, 0]
    )
    assert state[f"{SRD}.entities"]["guard"]["speed"] == 30


def test_paid_form_and_independent_slow_split_one_physical_journey():
    state, _, params = courier._courier(destination=(20000, 0, 0))
    state[f"{SRD}.entities"]["guard"]["ability_scores"] = dict.fromkeys(
        ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"], 12
    )
    _cast(state, params, "gaseous_form", "mist_caster")
    _cast(state, params, "slow", "slow_caster")
    _run("advance_srd_lasting_time", state, elapsed_rounds=605)
    assert courier._position(state) == pytest.approx(
        [20 + (264000 * (10 + 1180) + 132000 * 10) / 28800, 0, 0]
    )


def test_pure_query_cannot_use_an_unowned_projected_modifier():
    state, _, _ = courier._courier(destination=(10000, 0, 0), flying_speed=60)
    state[f"{SRD}.entities"]["guard"].update(
        spell_slow=True, spell_haste=True, spell_freedom_of_movement=True
    )
    result = _query(state, 0, 20)
    assert result["_courier_source_slow"] is result["_courier_source_haste"] is False
    assert result["_courier_source_freedom"] is False
    assert result["_courier_pace"] == pytest.approx(264000 / 28800)


def test_actual_combat_move_spends_budget_and_elapsed_combat_time_is_not_banked():
    from sylvia_gm.domain.errors import RulesetPreconditionRejected

    state, _, _ = courier._courier(destination=(10000, 0, 0))
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        movement_remaining=30,
        movement_spent=0,
        movement_mode="walk",
        dashes_taken=0,
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    assert courier._position(state) == [30, 0, 0]
    assert state[f"{SRD}.combat"]["movement_spent"] == 10
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert courier._position(state) == [30, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["animal_messenger"]["last_tick"] == 20
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("move_srd_scene_creature", state, actor_state="guard", destination=[100, 0, 0])
    assert state == before


def test_admitted_equipment_change_updates_actual_later_courier_pace():
    state, data, params = courier._case("animal_messenger")
    params["divination_destination"] = [10000, 0, 0]
    profile = {
        "strength": 10,
        "dexterity": 12,
        "unarmored_ac": state[f"{SRD}.entities"]["guard"]["armor_class"],
        "speed": 30,
        "climb_speed": 0,
        "swim_speed": 0,
        "armor_training": ["heavy"],
        "armor": [],
        "shield": [],
    }
    _run("initialize_srd_npc_equipment", state, actor_state="guard", equipment=[profile])
    state[f"{SRD}.entities"]["guard"].update(inventory=["plate_armor"], exhaustion_level=1)
    _run("cast_spell", state, participant_data=data, **params)
    assert _query(state, 0, 2)["_courier_pace"] == pytest.approx(132000 / 28800 * 25 / 30)
    _run(
        "change_srd_equipment",
        state,
        actor_state="guard",
        slot="armor",
        item=[{"inventory_item": "plate_armor", "armor_type": "plate_armor", "magic_bonus": 0}],
    )
    actor = state[f"{SRD}.entities"]["guard"]
    assert actor["speed"] == 20 and actor["armor_speed_penalty"] == 10
    now = state[f"{SRD}.state"]["lasting_tick"]
    assert _query(state, now, now + 2)["_courier_pace"] == pytest.approx(132000 / 28800 * 15 / 20)
    position = courier._position(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert courier._position(state) == pytest.approx(
        [position[0] + 132000 * 2 / 28800 * 15 / 20, 0, 0]
    )


def test_strict_public_paid_slow_source_reopens_retries_and_expires_during_real_travel(
    tmp_path, monkeypatch
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application import bundled_packages
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_dream_spell as dream
    from tests import test_srd_public_spell_casting as public
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_npc_spell_casting import _act

    def case(spell):
        state, data, parameters = courier._case(spell)
        parameters["divination_destination"] = [10000, 0, 0]
        state[f"{SRD}.entities"]["guard"]["flying_speed"] = 60
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity("pace_caster", [20, 0, 0], participant_ref="pace_caster")
        )
        return state, data, parameters

    monkeypatch.setattr(bundled_packages, "_srd_drafts", dream._linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", _runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", set())
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", case)
    database = tmp_path / "interval-courier.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "animal_messenger")
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert "cast_spell_outside_combat" in names
        assert "project_srd_courier_modifiers_at_tick" not in names
        messenger, messenger_command = _act(owner, "cast_spell_outside_combat", **parameters)
        prepared, _ = _act(owner, "prepare_srd_creature", **_npc("slow"))
        casting = body._gas_casting(parameters)
        casting.update(spell="slow", caster_state="pace_caster")
        slow, slow_command = _act(owner, "cast_spell_outside_combat", **casting)
        assert messenger.status is prepared.status is slow.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["pace_caster"]["spell_uses_remaining"]["slow"] == 0
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(messenger_command) == messenger
        assert owner.execute_action(slow_command) == slow
        assert owner.snapshot("casting").state == saved.state
        elapsed, elapsed_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=15)
        assert elapsed.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert list(courier._position(after.state)) == pytest.approx(
            [20 + 264000 * (20 / 2 + 10) / 28800, 0, 0]
        )
        assert after.state[f"{SRD}.state"]["lasting_tick"] == 30
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(elapsed_command) == elapsed
        assert owner.snapshot("casting").state == after.state


def test_repeat_binder_preserves_exact_tool_bodies():
    runtime, content = _runtime()
    assert _load_runtime(bind_runtime_consumers(content.decode())) == runtime
    for name in (
        "project_srd_courier_modifiers_at_tick",
        "qualify_srd_courier_actor_modifier_at_tick",
    ):

        def walk(effects):
            for effect in effects:
                if "set" in effect:
                    assert effect["set"]["field"].startswith("_")
                for branch in ("on_true", "on_false"):
                    walk(effect.get(branch, []))
                walk(effect.get("for_each", {}).get("do", []))

        walk(runtime["tools"][name]["effects"])
