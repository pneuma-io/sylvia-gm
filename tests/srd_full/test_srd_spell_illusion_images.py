"""Additional images retain actual sources, target knowledge, hit dice and remote geometry."""

from functools import lru_cache
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_illusion_images import PROFILES, PUBLIC_TOOLS, extend_runtime, validate_profiles
from scripts.srd_full.srd_reaction_spells import finalize_pending_spell_guards
from scripts.srd_full.srd_spell_damage_consumers import extend_runtime as extend_damage
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_appearance as appearance
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_illusions import _case as _image_case
from tests.srd_full.test_srd_spell_illusions import _summary
from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall


@lru_cache
def _runtime():
    _, content = appearance._runtime()
    text = extend_runtime(content.decode())
    text = extend_damage(text)
    text = refresh_exploration_runtime(text)
    text = finalize_pending_spell_guards(text)
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "acid_arrow" in parameter.get("enum_values", []):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    content = yaml.dump(
        runtime, Dumper=getattr(yaml, "CSafeDumper", yaml.SafeDumper), sort_keys=False
    ).encode()
    return runtime, content


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


def _case(spell, **changes):
    state, data, parameters = _image_case(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(_entity("seen-hill", [30, 0, 0], is_object=True, seen_by=["hero"]))
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["intelligence"] = -100
    parameters.update(
        illusion_position=[20, 0, 0],
        illusion_size=[10, 10, 10],
        illusion_description="A dangerous burning bridge",
        illusion_sound="Crackling flame",
        illusion_temperature="Warm air",
        phantasm_dangerous=True,
        phantasm_damage_appearance="fire",
        project_image_location="seen-hill",
    )
    if spell != "phantasmal_force":
        parameters.pop("target", None)
        parameters.pop("target_state", None)
    parameters.update(changes)
    return state, data, parameters


def _cast(spell, **changes):
    state, data, parameters = _case(spell, **changes)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    records = state[f"{SRD}.state"]["illusion_spell_sources"]
    source = next(reversed(records.values()))
    return state, data, parameters, source, result


def _source(state, source):
    return state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]


def _scene_entity(state, identity):
    return next(
        entity
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entity["id"] == identity
    )


@pytest.mark.parametrize("spell", PROFILES)
def test_actual_paid_image_cast_and_exact_printed_lifetime(spell):
    state, _, _, source, result = _cast(spell)
    assert source["spell"] == spell and source["installed"]
    assert source["expires_tick"] - source["started_tick"] == PROFILES[spell]["duration_rounds"] * 2
    assert source["spell_level"] == PROFILES[spell]["base_level"]
    assert "Source-owned image installed" in {
        row["value"] for row in result.result["context"]["_spell_summary_rows"]
    }
    if spell != "mirror_image":
        assert state[f"{SRD}.entities"]["hero"]["illusion_concentration_id"] == source["id"]
    else:
        assert source["remaining_duplicates"] == 3
        assert not source["concentration"]
    if spell != "project_image":
        assert not any(
            e["id"] == source["id"] for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        )


def _mirror(state):
    return _run("resolve_srd_mirror_image_hit", state, target_state="hero", attacker_state="guard")


def test_mirror_actual_source_rolls_per_remaining_duplicate_and_exact_expiry(monkeypatch):
    state, _, _, source, _ = _cast("mirror_image")
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    for remaining in [2, 1, 0]:
        result = _mirror(state)
        assert result.result["context"]["_mirror_redirected"]
        assert len(result.result["context"]["_mirror_dice"]) == remaining + 1
        assert _source(state, source)["remaining_duplicates"] == remaining
    assert not _source(state, source)["installed"]
    assert not _mirror(state).result["context"]["_mirror_redirected"]


@pytest.mark.parametrize("exception", ["blinded", "blindsight", "truesight"])
def test_mirror_actual_native_attacker_exceptions_do_not_roll_or_destroy(exception):
    state, _, _, source, _ = _cast("mirror_image")
    actor = state[f"{SRD}.entities"]["guard"]
    if exception == "blinded":
        actor["conditions"].append("blinded")
    elif exception == "blindsight":
        actor["blindsight_feet"] = 10
    else:
        actor["truesight_feet"] = 10
    result = _mirror(state).result["context"]
    assert not result["_mirror_redirected"] and result["_mirror_dice"] == []
    assert source["remaining_duplicates"] == 3


def test_phantasm_actual_failed_save_target_only_study_and_physical_rationalization():
    state, _, _, source, _ = _cast("phantasmal_force")
    result = _summary(
        _run("observe_srd_phantasm", state, observer_state="guard", illusion_id=source["id"])
    )
    assert result["Appearance"] == "A dangerous burning bridge"
    assert "rationalized" in result["Belief"]
    with pytest.raises(RulesetPreconditionRejected, match="Only the affected target"):
        _run("observe_srd_phantasm", state, observer_state="other", illusion_id=source["id"])
    with pytest.raises(RulesetPreconditionRejected, match="Physical image volume"):
        _run("touch_srd_illusion", state, observer_state="guard", illusion_id=source["id"])
    actor = state[f"{SRD}.entities"]["guard"]
    actor["skill_check_modifiers"] = {"investigation": 100}
    studied = _run(
        "study_srd_phantasm_creature", state, observer_state="guard", illusion_id=source["id"]
    )
    assert _summary(studied)["Recognized"] and not _source(state, source)["installed"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_phantasm_successful_actual_save_and_current_truesight_do_not_install():
    for exception in ["saving_throw", "truesight"]:
        state, data, parameters = _case("phantasmal_force")
        guard = state[f"{SRD}.entities"]["guard"]
        if exception == "saving_throw":
            guard["saving_throw_modifiers"]["intelligence"] = 100
        else:
            guard["truesight_feet"] = 30
        result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        assert result.result["context"]["_image_summary_rows"][0]["value"]
        assert not state[f"{SRD}.state"].get("illusion_spell_source_ids")


def _turn(state, round_number=1):
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        round=round_number,
        turn_index=0,
        order=["hero", "guard"],
        action_available=True,
    )


def test_phantasm_actual_turn_distance_psychic_packet_and_final_temp_hp_feedback(monkeypatch):
    state, _, _, source, _ = _cast("phantasmal_force")
    _turn(state)
    guard = state[f"{SRD}.entities"]["guard"]
    guard["temp_hp"] = 30
    original_hp = guard["hp"]
    monkeypatch.setattr(dice, "_rng", Random(1))
    result = _summary(
        _run("damage_srd_phantasm", state, caster_state="hero", illusion_id=source["id"])
    )
    assert result["Actual HP and Temp HP lost"] > 0
    assert result["Perceived damage kind"] == "fire"
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == original_hp
    assert 30 - guard["temp_hp"] == result["Actual HP and Temp HP lost"]
    with pytest.raises(RulesetPreconditionRejected, match="already dealt damage"):
        _run("damage_srd_phantasm", state, caster_state="hero", illusion_id=source["id"])
    _turn(state, 2)
    entity = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard"
    )
    shape = entity["footprint"]
    extent = shape["half_extents"][0] if shape["kind"] == "box" else shape["radius"]
    entity["position"] = [30 + extent + 0.001, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="within five feet"):
        _run("damage_srd_phantasm", state, caster_state="hero", illusion_id=source["id"])
    entity["position"] = [30 + extent, 0, 0]
    assert (
        _summary(_run("damage_srd_phantasm", state, caster_state="hero", illusion_id=source["id"]))[
            "Actual HP and Temp HP lost"
        ]
        > 0
    )


def test_project_actual_seen_location_ignores_wall_magic_action_and_remote_senses():
    state, data, parameters = _case("project_image")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(_wall(1))
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = next(reversed(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    proxy = _scene_entity(state, source["id"])
    assert proxy["position"] == [30, 0, 0]
    assert proxy["metadata"]["intangible"] and proxy["movement"] == "passable"
    perceived = _summary(
        _run("perceive_srd_project_image", state, caster_state="hero", illusion_id=source["id"])
    )
    assert any("guard" in message for message in perceived["Visible subjects"])
    assert not any("hero" in message for message in perceived["Visible subjects"])
    _turn(state)
    _run(
        "control_srd_project_image",
        state,
        caster_state="hero",
        illusion_id=source["id"],
        destination=[90, 0, 0],
        speech="Welcome",
        gesture="wave",
    )
    assert _scene_entity(state, source["id"])["position"] == [90, 0, 0]
    assert not state[f"{SRD}.combat"]["action_available"]
    assert _source(state, source)["speech"] == "Welcome"


def test_project_actual_range_prior_sight_study_touch_hollow_and_exact_damage():
    state, _, _, source, _ = _cast("project_image")
    _scene_entity(state, "guard")["position"] = [25, 0, 0]
    _run("touch_srd_illusion", state, observer_state="guard", illusion_id=source["id"])
    observed = _summary(
        _run("observe_srd_illusion", state, observer_state="guard", illusion_id=source["id"])
    )
    assert observed["Visual transparent"] and observed["Sensory quality"] == "hollow"
    zero = _run(
        "end_srd_project_image_damage", state, subject_id=source["proxy_id"], effective_damage=0
    )
    assert not zero.result["context"]["_project_damage_ended"] and source["installed"]
    wrong = _run("end_srd_project_image_damage", state, subject_id="other", effective_damage=1)
    assert not wrong.result["context"]["_project_damage_ended"]
    actual = _run(
        "end_srd_project_image_damage", state, subject_id=source["proxy_id"], effective_damage=1
    )
    assert (
        actual.result["context"]["_project_damage_ended"]
        and not _source(state, source)["installed"]
    )
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_image_emitter_repeat_profiles_and_hidden_hit_query():
    runtime, content = _runtime()
    assert _load_runtime(extend_runtime(content.decode())) == runtime
    validate_profiles(PROFILES)
    assert runtime["tools"]["resolve_srd_mirror_image_hit"]["metadata"]["category"] == "internal"
    assert PUBLIC_TOOLS.isdisjoint({"resolve_srd_mirror_image_hit", "end_srd_project_image_damage"})


def test_public_project_cast_control_reopen_retry_and_exact_lifetime(tmp_path, monkeypatch):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(appearance, "_runtime", _runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", appearance.PUBLIC_TOOLS | PUBLIC_TOOLS)
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / "project.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "project_image")
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        source = next(
            reversed(list(before.state[f"{SRD}.state"]["illusion_spell_sources"].values()))
        )
        names = {tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")}
        assert names >= PUBLIC_TOOLS and "resolve_srd_mirror_image_hit" not in names
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == before.state
        moved, move_command = _act(
            owner,
            "control_srd_project_image",
            caster_state="hero",
            illusion_id=source["id"],
            destination=[40, 0, 0],
            speech="Hello",
        )
        assert moved.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(move_command) == moved
        assert owner.snapshot("casting").state == saved.state
        observed, _ = _act(
            owner, "perceive_srd_project_image", caster_state="hero", illusion_id=source["id"]
        )
        assert _summary(observed)["Visible subjects"]
        _act(owner, "advance_srd_lasting_time", elapsed_rounds=14400)
        ended = owner.snapshot("casting").state[f"{SRD}.state"]["illusion_spell_sources"][
            source["id"]
        ]
        assert not ended["installed"]
