"""Pure body queries and actual courier travel split genuine form lifetime boundaries."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_courier_body_movement import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_wave6_transformations as forms
from tests import test_srd_spell_wave7_courier as courier
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _entity as scene_entity


@lru_cache
def _runtime():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = bind_runtime_consumers(source.read_text(encoding="utf-8"))
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


def _query(state, tick, end=50000):
    before = deepcopy(state)
    context = _run(
        "project_srd_body_movement_at_tick",
        state,
        target_state="guard",
        at_tick=tick,
        interval_end=end,
    ).result["context"]
    assert state == before
    return context


def _install(state, spell, form_id="", mode="", caster="hero"):
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state=caster,
        lasting_state="state",
        spell=spell,
        form_id=form_id,
        mode=mode,
    )
    return state[f"{SRD}.entities"]["guard"]["form_current_source_id"]


def test_native_body_query_has_no_source_or_clock_side_effects():
    state, _, _ = courier._courier(flying_speed=25, swim_speed=15, climb_speed=10)
    result = _query(state, 400)
    assert (
        result["_body_walk_speed"],
        result["_body_fly_speed"],
        result["_body_swim_speed"],
        result["_body_climb_speed"],
    ) == (30, 25, 15, 10)
    assert result["_body_source_id"] is None and result["_body_next_tick"] == 50000


@pytest.mark.parametrize("form_id", ["hawk", "camel"])
def test_actual_beast_source_expires_to_saved_native_body_without_mutating_source(form_id):
    state, _, _ = forms._setup("animal_shapes")
    state[f"{SRD}.entities"]["guard"].update(flying_speed=15, swim_speed=12, can_hover=True)
    source_id = _install(state, "animal_shapes", form_id)
    source = state[f"{SRD}.state"]["transformation_sources"][source_id]
    result = _query(state, source["started_tick"])
    profile = _runtime()[0]["definitions"]["beast_forms"][form_id]
    assert result["_body_walk_speed"] == profile["walk_speed"]
    assert result["_body_fly_speed"] == profile["other_speeds"].get("fly", 0)
    assert result["_body_source_id"] == source_id
    assert result["_body_next_tick"] == source["expires_tick"]
    result = _query(state, source["expires_tick"])
    assert (result["_body_walk_speed"], result["_body_fly_speed"], result["_body_swim_speed"]) == (
        30,
        15,
        12,
    )
    assert result["_body_can_hover"] is True and result["_body_source_id"] is None
    assert state[f"{SRD}.state"]["transformation_sources"][source_id]["installed"] is True


def test_newer_paid_polymorph_expiry_selects_older_actual_source_then_native_body():
    state, data, parameters = forms._setup(
        "polymorph", transformation_form="hawk", forego_save_targets=["guard"]
    )
    older = _install(state, "animal_shapes", "camel")
    forms._cast(state, data, parameters)
    newer = state[f"{SRD}.entities"]["guard"]["form_current_source_id"]
    sources = state[f"{SRD}.state"]["transformation_sources"]
    assert _query(state, 0)["_body_source_id"] == newer
    result = _query(state, sources[newer]["expires_tick"])
    assert result["_body_source_id"] == older and result["_body_walk_speed"] == 50
    assert result["_body_fly_speed"] == 0
    assert _query(state, sources[older]["expires_tick"])["_body_walk_speed"] == 30
    state[f"{SRD}.entities"]["guard"]["temp_hp"] = 0
    assert _query(state, 0)["_body_source_id"] == older


def test_paid_non_beast_true_polymorph_pure_completion_retains_exact_body_and_source():
    state, data, parameters = forms._setup(
        "true_polymorph", transformation_form="owlbear", forego_save_targets=["guard"]
    )
    forms._cast(state, data, parameters)
    source_id = state[f"{SRD}.entities"]["guard"]["form_current_source_id"]
    source = state[f"{SRD}.state"]["transformation_sources"][source_id]
    result = _query(state, source["expires_tick"])
    assert result["_body_source_id"] == source_id
    assert (result["_body_walk_speed"], result["_body_climb_speed"]) == (40, 40)
    assert source["permanent"] is False
    assert state[f"{SRD}.state"]["lasting_tick"] == 0


@pytest.mark.parametrize("direction", ["to_normal", "to_cloud"])
def test_wind_transition_uses_actual_timer_and_removes_only_its_owned_stunned(direction):
    state, _, _ = forms._setup("wind_walk")
    source_id = _install(state, "wind_walk", mode="cloud")
    if direction == "to_cloud":
        _run("change_srd_transformation", state, actor_state="guard")
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    _run("change_srd_transformation", state, actor_state="guard")
    source = state[f"{SRD}.state"]["transformation_sources"][source_id]
    now = state[f"{SRD}.state"]["lasting_tick"]
    assert source["mode"] == direction
    before = _query(state, now)
    assert "stunned" in before["_body_conditions"]
    assert before["_body_next_tick"] == source["transition_tick"]
    after = _query(state, source["transition_tick"])
    assert "stunned" not in after["_body_conditions"]
    assert (after["_body_walk_speed"], after["_body_fly_speed"]) == (
        (30, 0) if direction == "to_normal" else (0, 300)
    )
    state[f"{SRD}.entities"]["guard"]["timed_condition_unowned_stunned"] = True
    assert "stunned" in _query(state, source["transition_tick"])["_body_conditions"]


def _gas_preparation():
    return {
        "creature_id": "mist_caster",
        "name": "Source mist caster",
        "source": "Synthetic public NPC caster with admitted material exemption",
        "armor_class": 12,
        "hit_points": 25,
        "speed": 30,
        "initiative_modifier": 0,
        "size_rank": 2,
        "constitution_save": 3,
        "spellcasting_ability": "wisdom",
        "spellcasting_modifier": 3,
        "spell_attack_bonus": 7,
        "spell_save_dc": 15,
        "spell_component_exemptions": ["material"],
        "spells": [{"spell": "gaseous_form", "uses_per_day": 1}],
    }


def _gas_casting(params):
    casting = {
        key: value
        for key, value in params.items()
        if not key.startswith(("divination", "information"))
    }
    for key in ("caster", "slot_resource", "material_item_ids"):
        casting.pop(key, None)
    casting.update(
        spell="gaseous_form",
        caster_state="mist_caster",
        target="guard",
        target_willing=True,
        caster_can_touch_target=True,
    )
    return casting


def _gas_courier():
    state, _, params = courier._courier(destination=(20000, 0, 0))
    state[f"{SRD}.entities"]["guard"].update(
        ability_scores=dict.fromkeys(
            ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"], 12
        )
    )
    _run("prepare_srd_creature", state, **_gas_preparation())
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        scene_entity("mist_caster", [20, 0, 0], participant_ref="mist_caster")
    )
    _run("cast_spell_outside_combat", state, **_gas_casting(params))
    return state


def test_paid_gaseous_courier_clock_splits_form_flight_expiry_then_actual_native_walk():
    state = _gas_courier()
    source_id = state[f"{SRD}.entities"]["guard"]["form_current_source_id"]
    source = state[f"{SRD}.state"]["transformation_sources"][source_id]
    assert source["expires_tick"] == 1200
    assert _query(state, 0)["_body_fly_speed"] == 10
    _run("advance_srd_lasting_time", state, elapsed_rounds=700)
    assert courier._position(state) == pytest.approx(
        [20 + (264000 * 1200 + 132000 * 200) / 28800, 0, 0]
    )
    assert state[f"{SRD}.state"]["lasting_tick"] == 1400
    assert state[f"{SRD}.entities"]["guard"]["flying_speed"] in (None, 0)
    assert state[f"{SRD}.entities"]["mist_caster"]["spell_uses_remaining"]["gaseous_form"] == 0


@pytest.mark.parametrize("wrong_binding", ["caster_state", "target_state"])
def test_body_qualification_rejects_other_actor_bound_to_actual_saved_source(wrong_binding):
    state, _, _ = forms._setup("animal_shapes")
    source_id = _install(state, "animal_shapes", "hawk")
    params = {
        "target_state": "guard",
        "caster_state": "hero",
        "source_id": source_id,
        "at_tick": 0,
        "interval_end": 50000,
    }
    params[wrong_binding] = "other"
    before = deepcopy(state)
    result = _run("qualify_srd_body_movement_source_at_tick", state, **params).result["context"]
    assert result["_body_record_active"] is False
    assert state == before


def test_body_binder_repeats_without_dropping_journey_or_independent_speed_controls():
    runtime, content = _runtime()
    assert _load_runtime(bind_runtime_consumers(content.decode())) == runtime
    journey = runtime["tools"]["progress_srd_animal_messenger_journey"]
    assert "progress_srd_animal_messenger_interval" in str(journey)
    pace = str(runtime["tools"]["project_srd_animal_messenger_pace"])
    for retained in (
        "target_state.spell_slow",
        "target_state.spell_haste",
        "target_state.exhaustion_level",
        "target_state.control_confusion_active",
    ):
        assert retained in pace


def test_strict_paid_public_form_courier_reopens_retries_and_consumes_later_clock(
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
        parameters["divination_destination"] = [20000, 0, 0]
        state[f"{SRD}.entities"]["guard"]["ability_scores"] = dict.fromkeys(data["abilities"], 12)
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            scene_entity("mist_caster", [20, 0, 0], participant_ref="mist_caster")
        )
        return state, data, parameters

    monkeypatch.setattr(bundled_packages, "_srd_drafts", dream._linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", _runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", set())
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", case)
    database = tmp_path / "body-courier.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "animal_messenger")
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert {"cast_spell_outside_combat", "advance_srd_lasting_time"} <= names
        assert "project_srd_body_movement_at_tick" not in names
        messenger, messenger_command = _act(owner, "cast_spell_outside_combat", **parameters)
        prepared, _ = _act(owner, "prepare_srd_creature", **_gas_preparation())
        gas, gas_command = _act(owner, "cast_spell_outside_combat", **_gas_casting(parameters))
        assert messenger.status is prepared.status is gas.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert (
            saved.state[f"{SRD}.entities"]["mist_caster"]["spell_uses_remaining"]["gaseous_form"]
            == 0
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(messenger_command) == messenger
        assert owner.execute_action(gas_command) == gas
        assert owner.snapshot("casting").state == saved.state
        elapsed, elapsed_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=700)
        assert elapsed.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert list(courier._position(after.state)) == pytest.approx(
            [20 + (264000 * 1200 + 132000 * 200) / 28800, 0, 0]
        )
        assert after.state[f"{SRD}.state"]["lasting_tick"] == 1400
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(elapsed_command) == elapsed
        assert owner.snapshot("casting").state == after.state
