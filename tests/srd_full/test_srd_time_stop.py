"""Time Stop proves real extra budgets, frozen others, actual scene departure and public retry."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_reaction_spells import (
    extend_runtime as extend_root_reactions,
)
from scripts.srd_full.srd_reaction_spells import (
    finalize_pending_spell_guards,
    normalize_pending_spell_guards,
)
from scripts.srd_full.srd_time_stop import PROFILES, PUBLIC_TOOLS, bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_appearance as appearance
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_illusions import _case as _base_case
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = extend_runtime(normalize_pending_spell_guards(source.read_text(encoding="utf-8")))
    text = refresh_exploration_runtime(text)
    text = bind_runtime_consumers(text)
    text = extend_root_reactions(text)
    text = finalize_pending_spell_guards(text)
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    return runtime, yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False).encode()


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


def _case(spell="time_stop", **choices):
    state, data, parameters = _base_case(spell, **choices)
    parameters.pop("target", None)
    for parameter in list(parameters):
        if parameter.startswith("illusion_"):
            parameters.pop(parameter)
    return state, data, parameters


def _cast(encounter=False):
    state, data, parameters = _case()
    combat = state[f"{SRD}.combat"]
    if encounter:
        combat.update(
            encounter_active=True,
            turn_active=True,
            active_actor="hero",
            current_actor="hero",
            phase="turn_active",
            round=4,
            turn_index=0,
            order_first="hero",
            order_second="guard",
            movement_remaining=41,
            movement_spent=19,
            action_available=True,
            normal_action_spent=False,
        )
        state[f"{SRD}.entities"]["hero"]["turns_started"] = 7
    state[f"{SRD}.entities"]["guard"]["turns_started"] = 3
    result = _run(
        "cast_spell" if encounter else "cast_spell_outside_combat",
        state,
        participant_data=data,
        **parameters,
    )
    return state, data, parameters, result


def _grant_spell(state, data, spell, **choices):
    """Reuse authentic preparation/material admission without unrelated image choices."""
    from tests.srd_full.test_srd_spell_illusions import _grant_other_spell

    parameters = _grant_other_spell(state, data, spell, **choices)
    return {key: value for key, value in parameters.items() if not key.startswith("illusion_")}


def _stop(state):
    return state[f"{SRD}.state"]["time_stop_source"]


def _begin(state):
    return _run("begin_srd_time_stop_turn", state)


def _end(state):
    return _run("end_srd_time_stop_turn", state)


@pytest.mark.parametrize("encounter", [False, True])
def test_actual_paid_cast_extra_turns_original_budget_and_frozen_clock(encounter):
    state, _, _, result = _cast(encounter)
    source = _stop(state)
    suspended = deepcopy(source["suspended_combat"])
    assert 2 <= source["extra_turns"] <= 5
    assert source["roll"]["modifier"] == 1
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    start_tick = state[f"{SRD}.state"]["lasting_tick"]
    caster_start = state[f"{SRD}.entities"]["hero"].get("turns_started", 0)
    for _ in range(source["extra_turns"]):
        _begin(state)
        assert state[f"{SRD}.combat"]["action_available"]
        assert state[f"{SRD}.combat"]["movement_spent"] == 0
        _run("dash_combat_action", state, actor="hero", actor_state="hero")
        assert not state[f"{SRD}.combat"]["action_available"]
        _end(state)
        assert state[f"{SRD}.state"]["lasting_tick"] == start_tick
        assert state[f"{SRD}.entities"]["guard"]["turns_started"] == 3
    assert not _stop(state)["active"] and _stop(state)["end_reason"] == "extra_turns_complete"
    assert state[f"{SRD}.entities"]["hero"]["turns_started"] == caster_start + source["extra_turns"]
    for field in [
        "round",
        "turn_index",
        "current_actor",
        "encounter_active",
        "turn_active",
        "active_actor",
        "action_available",
        "normal_action_spent",
        "movement_remaining",
        "movement_spent",
    ]:
        assert state[f"{SRD}.combat"].get(field) == suspended.get(field)
    if encounter:
        assert not state[f"{SRD}.combat"]["action_available"]
        assert state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"]


def test_noncasters_actions_reactions_clock_and_initiative_do_not_advance():
    state, _, _, _ = _cast(True)
    _begin(state)
    for tool, params in [
        ("check_srd_active_control_action", {"actor_state": "guard", "action_kind": "action"}),
        (
            "consume_srd_reaction",
            {"actor_state": "guard", "reaction": "opportunity_attack", "lasting_state": "state"},
        ),
        ("advance_srd_lasting_clock", {"elapsed_ticks": 1, "lasting_state": "state"}),
        ("advance_srd_lasting_time", {"elapsed_rounds": 1}),
    ]:
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            _run(tool, state, **params)
        assert state == before
    _end(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Time Stop"):
        _run("advance_combat_encounter_turn", state)
    assert state == before


def test_genuine_movement_boundary_1000_inclusive_then_early_resume():
    state, _, _, _ = _cast(True)
    state[f"{SRD}.entities"]["hero"]["speed"] = 1200
    state[f"{SRD}.entities"]["guard"]["speed"] = 1200
    _begin(state)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 1000, 0])
    assert _stop(state)["active"]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 1001, 0])
    assert not _stop(state)["active"] and not _stop(state)["ending"]
    assert _stop(state)["end_reason"] == "departure"
    assert state[f"{SRD}.combat"]["movement_spent"] == 19
    assert state[f"{SRD}.combat"]["movement_remaining"] == 1181
    hero = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "hero")
    assert hero["position"] == [0, 1001, 0]


def test_packets_outside_new_spell_action_preserve_time_stop_and_actual_damage():
    state, _, _, _ = _cast(True)
    _begin(state)
    _run(
        "apply_srd_damage",
        state,
        target="hero",
        amount=1,
        adjustment=0,
        damage_type="psychic",
        source_state_id="hero",
    )
    assert _stop(state)["active"]
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=0,
        adjustment=0,
        damage_type="psychic",
        source_state_id="hero",
    )
    assert _stop(state)["active"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 19
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=4,
        adjustment=0,
        damage_type="psychic",
        source_state_id="hero",
    )
    assert _stop(state)["active"] and not _stop(state)["ending"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 16
    assert state[f"{SRD}.combat"]["action_available"]


def test_actual_new_darkvision_grant_ends_time_stop_and_preserves_installed_source():
    state, data, _, _ = _cast(True)
    parameters = _grant_spell(state, data, "darkvision")
    _begin(state)
    _run("cast_spell", state, participant_data=data, **parameters)
    assert not _stop(state)["active"] and not _stop(state)["ending"]
    assert not _stop(state)["spell_action_active"]
    assert _stop(state)["end_reason"] == "other_creature"
    row = next(r for r in state[f"{SRD}.state"]["turn_effects"] if r["kind"] == "timed_senses")
    assert row["spell"] == "darkvision" and row["target"] == "guard"
    assert not state[f"{SRD}.combat"]["action_available"]


def test_actual_new_healing_spell_ends_time_stop_only_after_real_hp_gain():
    state, data, _, _ = _cast(True)
    data["classes"].append({"id": "cleric", "level": 1})
    data["level"] += 1
    data["spellcasting_grants"].append(
        {"source": "cleric", "ability": "wisdom", "spells": ["healing_word"]}
    )
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("healing_word")
    _, _, parameters = cards._spell_case("healing_word")
    _begin(state)
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=4,
        adjustment=0,
        damage_type="psychic",
        source_state_id="hero",
    )
    assert _stop(state)["active"] and state[f"{SRD}.entities"]["guard"]["hp"] == 16
    _run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["guard"]["hp"] > 16
    assert not _stop(state)["active"] and not _stop(state)["ending"]
    assert not _stop(state)["spell_action_active"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2


def test_actual_extra_turn_weapon_hit_ends_time_stop_after_damage(monkeypatch):
    from random import Random

    from sylvia_gm import dice
    from tests.srd_full.test_srd_attack_budget import _attack

    state, data, _, _ = _cast(True)
    _begin(state)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _attack(state, data, attack_mode="thrown", attack_distance=20)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 19, result.result
    assert not _stop(state)["active"] and not _stop(state)["ending"]
    assert not _stop(state)["spell_action_active"]
    assert _stop(state)["end_reason"] == "other_creature"
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 19
    assert not state[f"{SRD}.combat"]["action_available"]


def test_actual_self_polymorph_reprojects_restriction_when_original_turn_resumes():
    state, data, _, _ = _cast(True)
    parameters = _grant_spell(
        state,
        data,
        "polymorph",
        target="hero",
        target_character="hero",
        transformation_form="brown_bear",
        forego_save_targets=["hero"],
    )
    _begin(state)
    _run("cast_spell", state, participant_data=data, **parameters)
    assert _stop(state)["active"]
    assert state[f"{SRD}.entities"]["hero"]["form_statistics_replaced"]
    while _stop(state)["active"]:
        _end(state)
        if _stop(state)["active"]:
            _begin(state)
    assert state[f"{SRD}.combat"]["active_actor_form_restricted"]
    assert state[f"{SRD}.combat"]["movement_spent"] == 19
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2


@pytest.mark.parametrize("held", ["carried_by", "worn_by"])
def test_actual_object_holder_qualification_preserves_self_and_loose_objects(held):
    state, _, _, _ = _cast(True)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] += [
        _entity("loose", [1, 0, 0], is_object=True),
        _entity("own", [1, 1, 0], is_object=True, **{held: "hero"}),
        _entity("held", [20, 1, 0], is_object=True, **{held: "guard"}),
    ]
    _begin(state)
    for subject in ["loose", "own"]:
        _run("observe_srd_time_stop_object_effect", state, subject_id=subject, source_state="hero")
        assert _stop(state)["active"]
    _run("observe_srd_time_stop_object_effect", state, subject_id="held", source_state="hero")
    assert not _stop(state)["active"] and _stop(state)["end_reason"] == "other_held_object"


def test_emitter_repeat_private_queries_and_derived_actor():
    runtime, content = _runtime()
    repeated = extend_runtime(normalize_pending_spell_guards(content.decode()))
    repeated = refresh_exploration_runtime(repeated)
    repeated = bind_runtime_consumers(repeated)
    repeated = extend_root_reactions(repeated)
    repeated = finalize_pending_spell_guards(repeated)
    parsed = _load_runtime(repeated)
    assert parsed == runtime
    for name in PUBLIC_TOOLS:
        assert "caster_state" not in _tool_schema(runtime["tools"][name])["properties"]
    assert (
        runtime["tools"]["observe_srd_time_stop_object_effect"]["metadata"]["category"]
        == "internal"
    )


def test_public_paid_cast_save_reopen_retry_extra_budget_and_finish(tmp_path, monkeypatch):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(appearance, "_runtime", _runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", PUBLIC_TOOLS)
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", _case)
    db = tmp_path / "time-stop.db"
    with AuthorityOwner.open(db, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "time_stop")
        paid, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert paid.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        count = saved.state[f"{SRD}.state"]["time_stop_source"]["extra_turns"]
    with AuthorityOwner.open(db, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command) == paid
        assert owner.snapshot("casting").state == saved.state
        assert {t.name for t in mechanics.list_tools("casting")} >= PUBLIC_TOOLS
        for _ in range(count):
            begun, begin_command = _act(owner, "begin_srd_time_stop_turn")
            assert begun.status is ActionStatus.COMMITTED
            snapshot = owner.snapshot("casting")
            assert mechanics.execute_action(begin_command) == begun
            assert owner.snapshot("casting").state == snapshot.state
            _act(owner, "dash_combat_action", actor="hero", actor_state="hero")
            _act(owner, "end_srd_time_stop_turn")
        final = owner.snapshot("casting").state
        assert not final[f"{SRD}.state"]["time_stop_source"]["active"]
        assert final[f"{SRD}.state"]["lasting_tick"] == saved.state[f"{SRD}.state"]["lasting_tick"]


def test_actual_extra_turn_spell_action_other_target_packet_resumes_original_budget():
    state, data, parameters = _case()
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        current_actor="hero",
        active_actor="hero",
        phase="turn_active",
        action_available=True,
        normal_action_spent=False,
        round=1,
        turn_index=0,
        order_first="hero",
        order_second="guard",
    )
    missile = _grant_spell(state, data, "magic_missile")
    missile["projectile_targets"] = ["guard"] * 3
    _run("cast_spell", state, participant_data=data, **parameters)
    _begin(state)
    result = _run("cast_spell", state, participant_data=data, **missile)
    assert not _stop(state)["active"] and not _stop(state)["ending"]
    assert _stop(state)["end_reason"] == "other_creature"
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 19
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    assert not state[f"{SRD}.combat"]["action_available"]
    assert result.result["status"] == "committed"


def test_other_creature_authentic_darkvision_lifetime_remains_frozen():
    state, data, parameters = _case()
    darkvision = _grant_spell(state, data, "darkvision")
    _run("cast_spell_outside_combat", state, participant_data=data, **darkvision)
    row = next(r for r in state[f"{SRD}.state"]["turn_effects"] if r["kind"] == "timed_senses")
    saved = deepcopy(row)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    count = _stop(state)["extra_turns"]
    for _ in range(count):
        _begin(state)
        _end(state)
    actual = next(r for r in state[f"{SRD}.state"]["turn_effects"] if r["kind"] == "timed_senses")
    assert actual == saved and actual["target"] == "guard"
    sensed = _run("resolve_srd_timed_senses", state, target_state="guard")
    assert sensed.result["context"]["_sense_darkvision"] >= 150


def test_inactive_family_preserves_ordinary_budget_with_no_saved_source():
    state = budget._state()
    _run("dash_combat_action", state, actor="hero", actor_state="hero")
    assert not state[f"{SRD}.combat"]["action_available"]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 60


def test_actual_counterspell_window_before_time_stop_and_frozen_later_reactor(monkeypatch):
    from random import Random

    from sylvia_gm import dice
    from tests.srd_full.test_srd_reaction_spells import _cast_parameters, _mage

    state, data, parameters = _case()
    _mage(state, "counterspell")
    _run(
        "prepare_srd_creature",
        state,
        creature_id="frozen_mage",
        name="Frozen Mage",
        source="Synthetic NPC: AC13, HP1000, Speed30, Intelligence spell DC15.",
        armor_class=13,
        hit_points=1000,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        constitution_save=0,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "counterspell", "uses_per_day": 2}],
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [10, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("frozen_mage", [15, 0, 0], participant_ref="frozen_mage", creature_type="humanoid")
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        phase="turn_active",
        action_available=True,
        normal_action_spent=False,
    )
    first = _run("begin_srd_spell_action", state, participant_data=data, **parameters)
    trigger = first.result["context"]["_pending_action_id"]
    assert state[f"{SRD}.state"].get("time_stop_source") is None
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _run("cast_srd_reaction_spell", state, **_cast_parameters("counterspell", trigger))
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["counterspell"] == 1
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=trigger)
    assert _stop(state)["active"]
    missile = _grant_spell(state, data, "magic_missile")
    _begin(state)
    next_cast = _run("begin_srd_spell_action", state, participant_data=data, **missile)
    later_trigger = next_cast.result["context"]["_pending_action_id"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Time Stop"):
        _run(
            "cast_srd_reaction_spell",
            state,
            **(_cast_parameters("counterspell", later_trigger) | {"caster_state": "frozen_mage"}),
        )
    assert state == before
    _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=later_trigger
    )
    assert not _stop(state)["active"] and not _stop(state)["ending"]
    assert state[f"{SRD}.entities"]["frozen_mage"]["spell_uses_remaining"]["counterspell"] == 2
