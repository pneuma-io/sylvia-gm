"""Awaken pays real casting time/materials and changes native later creature consumers."""

import ast
import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_awaken import PROFILE, SOURCE_SHA, bind_consumers, extend_runtime

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.schema_admission import validate_json_value
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import CommandRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes, participant_schema_sha256
from tests import test_srd_magic_jar as jar
from tests import test_srd_plant_growth as growth
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

SRD = jar.SRD


def _language_schema():
    """Read the actual selected package schema assignment, without running generation."""
    source = Path(__file__).resolve().parents[2] / "scripts/build_srd_5_2_1_mechanics.py"
    assignments = [
        node
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Assign)
        and any(
            ast.unparse(target) == "participant_schema['properties']['languages']"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    return ast.literal_eval(assignments[0].value)


def test_existing_character_language_admission_is_optional_unique_and_nonempty():
    schema = _language_schema()
    for actual in (["elvish", "draconic"], []):
        validate_json_value(actual, schema, label="Known languages")
    for actual in ([""], ["elvish", "elvish"], [1]):
        with pytest.raises(CommandRejected):
            validate_json_value(actual, schema, label="Known languages")


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    root = os.environ.get("FC14_AWAKEN_RUNTIME_ROOT")
    assert root, "Select the actual composed Awaken owner closure"
    monkeypatch.setenv("FC14_JAR_RUNTIME_ROOT", root)
    yield from jar._compiled.__wrapped__(monkeypatch)


def _run(tool_name, state, data, **params):
    return jar._run(tool_name, state, data, **params)


def _native_params(*, score=2, creature_type="beast"):
    return {
        "creature_id": "beast",
        "name": "Admitted Beast",
        "creature_type": creature_type,
        "source": "GM-adjudicated native test Beast: all scores10 except INT2; INT save-2 "
        "and Arcana-2 retain proficiency+2; other unlisted skills have no training.",
        "armor_class": 12,
        "hit_points": 20,
        "speed": 30,
        "size_rank": 1,
        "initiative_modifier": 0,
        "proficiency_bonus": 2,
        "native_hit_dice_count": 4,
        "native_hit_die_size": 8,
        "strength_score": 10,
        "dexterity_score": 10,
        "constitution_score": 10,
        "intelligence_score": score,
        "wisdom_score": 10,
        "charisma_score": 10,
        "strength_save": 0,
        "dexterity_save": 0,
        "constitution_save": 0,
        "intelligence_save": (score - 10) // 2 + 2,
        "wisdom_save": 0,
        "charisma_save": 0,
        "skill_checks": [{"skill": "arcana", "modifier": (score - 10) // 2 + 2}],
        "skill_proficiencies": ["arcana"],
        "saving_throw_proficiencies": ["intelligence"],
    }


def _case(*, score=2, creature_type="beast", natural=False, form="shrub"):
    state, data, params = jar.cards._spell_case("awaken")
    data["languages"] = ["elvish"]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    plant = _entity("sprig", [3, 0, 0], nonmagical_plant=True)
    plant["height"] = 1
    state[f"{SRD}.state"]["spatial_scene"] = _scene(
        _entity("hero", [0, 0, 0], participant_ref="hero"),
        _entity("beast", [3, 0, 0], participant_ref="beast"),
        _entity("guard", [20, 0, 0], participant_ref="guard"),
        plant,
    )
    _run(
        "prepare_srd_creature",
        state,
        data,
        **_native_params(score=score, creature_type=creature_type),
    )
    params.update(
        target="hero" if natural else "beast",
        awaken_language="elvish",
        awaken_subject="sprig" if natural else "",
        awaken_plant_form=form,
    )
    return state, data, params


def _cast(state, data, params):
    _run("begin_srd_spell_cast", state, data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        data,
        caster_state="hero",
        elapsed_rounds=4800,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return _run("cast_spell_outside_combat", state, data, **params)


def _target(state, name="beast"):
    return state[f"{SRD}.entities"][name]


def test_real_eight_hour_paid_native_effect_updates_save_skill_language_and_preserves_body():
    state, data, params = _case()
    before = deepcopy(_target(state))
    stock = _target(state, "hero")["spell_materials"][0]["quantity"]
    slot = _target(state, "hero")["resources"]["spell_slot_5"]
    _cast(state, data, params)
    target = _target(state)
    assert target["ability_scores"]["intelligence"] == 10
    assert target["saving_throw_modifiers"]["intelligence"] == 2
    assert target["skill_check_modifiers"]["arcana"] == 2
    assert target["languages"] == ["elvish"]
    for field in ("hp", "max_hp", "speed", "native_hit_dice", "resources", "skill_proficiencies"):
        assert target[field] == before[field]
    assert _target(state, "hero")["resources"]["spell_slot_5"] == slot - 1
    assert _target(state, "hero")["spell_materials"][0]["quantity"] == stock - 1
    source = state[f"{SRD}.state"]["timed_condition_sources"][target["awakened_charm_source_id"]]
    assert source["expires_tick"] - source["started_tick"] == 864000
    assert source["dispel_ongoing"] is False and source["ongoing_duration"] is False
    save = _run(
        "resolve_srd_condition_saving_throw",
        state,
        data,
        target="beast",
        ability="intelligence",
        condition="blinded",
        source_save_dc=15,
        circumstantial_modifier=0,
        disadvantage=False,
    )
    assert save.result["context"]["_combined_modifier"] == 2
    check = _run(
        "test_srd_awakened_intelligence",
        state,
        data,
        target_state="beast",
        skill="arcana",
        target_number=10,
    )
    assert check.result["context"]["_awaken_check_modifier"] == 2
    speech = _run(
        "communicate_srd_spell",
        state,
        data,
        listener_state="hero",
        speaker_state="beast",
        language="elvish",
    )
    assert speech.result["context"]["_language_understands"] is True


def test_charm_immunity_does_not_erase_instantaneous_intelligence_or_language():
    state, data, params = _case()
    _target(state)["condition_immunities"] = ["charmed"]
    _target(state)["languages"] = ["draconic"]
    _cast(state, data, params)
    target = _target(state)
    assert target["ability_scores"]["intelligence"] == 10
    assert target["languages"] == ["draconic", "elvish"]
    assert "charmed" not in target["conditions"]
    assert target["awakened_charm_source_id"] is None


@pytest.mark.parametrize("changes", [{"score": 4}, {"creature_type": "humanoid"}])
def test_printed_ineligible_creature_commits_costs_with_no_invented_effect(changes):
    state, data, params = _case(**changes)
    before = deepcopy(_target(state))
    slot = _target(state, "hero")["resources"]["spell_slot_5"]
    _cast(state, data, params)
    assert _target(state) == before
    assert _target(state, "hero")["resources"]["spell_slot_5"] == slot - 1


@pytest.mark.parametrize("form,hp,hd", [("shrub", 10, 3), ("tree", 59, 7)])
def test_natural_plant_has_one_actual_source_body_movement_and_human_senses(form, hp, hd):
    state, data, params = _case(natural=True, form=form)
    before = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["entities"][-1])
    _cast(state, data, params)
    target = _target(state, "sprig")
    assert target["creature_type"] == "plant" and target["hp"] == hp
    assert target["resources"]["hit_die"] == hd
    assert target["ability_scores"]["intelligence"] == 10
    assert target["languages"] == ["elvish"] and target["base_passive_perception"] == 10
    assert target["native_darkvision_feet"] == 0
    scene = state[f"{SRD}.state"]["spatial_scene"]
    plant = next(e for e in scene["entities"] if e["id"] == "sprig")
    assert plant["position"] == before["position"]
    assert plant["footprint"] == before["footprint"]
    assert plant["metadata"]["participant_ref"] == "sprig"
    assert plant["metadata"]["nonmagical_plant"] is False
    moved = _run(
        "move_srd_scene_creature",
        state,
        data,
        actor_state="sprig",
        destination=[10, 0, 0],
        mode="walk",
        difficult_terrain=False,
    )
    assert moved.result["context"]["_scene_movement_cost"] == 7
    assert (
        len([e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "sprig"])
        == 1
    )


def test_owner_emission_repeats_without_duplicate_permanent_or_damage_hooks():
    text = (Path(os.environ["FC14_AWAKEN_RUNTIME_ROOT"]) / "runtime/ruleset.yaml").read_text()
    first = bind_consumers(extend_runtime(text))
    second = bind_consumers(extend_runtime(first))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)
    assert PROFILE["duration_rounds"] == 0 and SOURCE_SHA


def test_actual_damage_retires_only_awaken_charm_for_current_caster_or_ally():
    state, data, params = _case()
    _cast(state, data, params)
    own = _target(state)["awakened_charm_source_id"]
    other = _run(
        "install_srd_timed_condition_source",
        state,
        data,
        target_state="beast",
        lasting_state="state",
        caster_state="guard",
        spell="charm_monster",
        condition="charmed",
        save_dc=15,
        duration_rounds=1000000,
        repeat_save_timing="none",
        control_profile="none",
    )
    sibling = other.result["context"]["_timed_condition_source_id"]
    _run("grant_temporary_hit_points", state, data, target="beast", amount=10, accept_new=True)
    for cause in ("", "guard"):
        _run(
            "apply_srd_qualifying_damage",
            state,
            data,
            target="beast",
            amount=1,
            adjustment=0,
            damage_type="force",
            source_state_id=cause,
        )
        assert state[f"{SRD}.state"]["timed_condition_sources"][own]["installed"] is True
    _run(
        "record_srd_creature_relationship",
        state,
        data,
        actor_state="hero",
        toward_state="guard",
        attitude="friendly",
        is_ally=True,
    )
    _run(
        "apply_srd_qualifying_damage",
        state,
        data,
        target="beast",
        amount=1,
        adjustment=0,
        damage_type="force",
        source_state_id="guard",
    )
    pool = state[f"{SRD}.state"]["timed_condition_sources"]
    assert pool[own]["installed"] is False and pool[sibling]["installed"] is True
    assert _target(state)["hp"] == 20 and _target(state)["temp_hp"] == 7
    assert _target(state)["ability_scores"]["intelligence"] == 10
    assert "charmed" in _target(state)["conditions"]


def test_actual_thirty_day_expiry_preserves_permanent_effects():
    state, data, params = _case()
    _cast(state, data, params)
    own = _target(state)["awakened_charm_source_id"]
    for _ in range(4):
        _run("advance_srd_lasting_time", state, data, elapsed_rounds=100000)
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=31999)
    assert "charmed" in _target(state)["conditions"]
    _run("advance_srd_lasting_time", state, data, elapsed_rounds=1)
    assert state[f"{SRD}.state"]["timed_condition_sources"][own]["installed"] is False
    assert "charmed" not in _target(state)["conditions"]
    assert _target(state)["languages"] == ["elvish"]
    assert _target(state)["ability_scores"]["intelligence"] == 10


@pytest.mark.parametrize("bad", ["language", "range", "missing_score", "body"])
def test_invalid_or_unsupported_actual_qualification_rolls_back_finish_cost(bad):
    state, data, params = _case()
    _run("begin_srd_spell_cast", state, data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        data,
        caster_state="hero",
        elapsed_rounds=4800,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    if bad == "language":
        params["awaken_language"] = "draconic"
    elif bad == "range":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [6, 0, 0]
    elif bad == "missing_score":
        _target(state)["ability_scores"] = None
    else:
        _target(state)["form_statistics_replaced"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, data, **params)
    assert state == before


def test_natural_tree_printed_attack_spends_real_action_and_rejects_altered_profile():
    state, data, params = _case(natural=True, form="tree")
    _cast(state, data, params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(e for e in scene["entities"] if e["id"] == "guard")["position"] = [10, 0, 0]
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="sprig",
        current_actor="sprig",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    attack = {
        "actor": "sprig",
        "target": "guard",
        "attack_name": "Slam",
        "attack_modifier": 6,
        "damage_expression": "3d6",
        "damage_modifier": 4,
        "damage_type": "bludgeoning",
        "attack_distance": 7,
        "attack_mode": "melee",
        "normal_range": 10,
        "long_range": 10,
        "actor_can_see_target": True,
        "target_can_see_actor": True,
        "target_is_charmer": False,
        "fear_source_in_sight": False,
        "advantage": False,
        "disadvantage": False,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="printed attack"):
        _run(
            "resolve_adjudicated_creature_attack", state, data, **(attack | {"attack_modifier": 30})
        )
    assert state == before
    result = _run("resolve_adjudicated_creature_attack", state, data, **attack)
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert _target(state, "guard")["hp"] < before[f"{SRD}.entities"]["guard"]["hp"]


def test_public_paid_eight_hour_awaken_reopens_for_language_check_and_idempotent_costs(
    tmp_path, monkeypatch
):
    from scripts.srd_full.srd_awaken import PUBLIC_TOOLS

    monkeypatch.setattr(growth, "PUBLIC_TOOLS", PUBLIC_TOOLS)
    growth._public_fixture(monkeypatch)
    drafts = growth.bundled_packages._srd_drafts

    def language_drafts():
        primary, reference = drafts()
        record = deepcopy(primary.record)
        artifact = next(
            a for a in record["participant_artifacts"] if a["kind"] == "player_character"
        )
        artifact["schema"]["properties"]["languages"] = _language_schema()
        artifact["schema_sha256"] = participant_schema_sha256(
            artifact["schema"],
            **{
                key: artifact[key]
                for key in (
                    "entity_state_schema",
                    "initial_state_calculations",
                    "initial_state_validation",
                )
            },
        )
        return NormalizedPackageDraft(record, primary.payloads, primary.format_version), reference

    monkeypatch.setattr(growth.bundled_packages, "_srd_drafts", language_drafts)
    # Campaign admission uses the same genuine schema and hash as the selected archive.
    record = deepcopy(language_drafts()[0].record)
    admission_root = tmp_path / "admission"
    record_path = admission_root / "rulesets/dnd-srd-5.2.1/package-record.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_bytes(canonical_json_bytes(record))
    monkeypatch.setattr(jar.public_casting, "ROOT", admission_root)
    monkeypatch.setattr(jar.public_casting, "_spell_case", lambda *_a, **_k: _case())
    database = tmp_path / "awaken.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = jar.public_casting._create_spell_campaign(owner, "awaken")
        initial = owner.snapshot("casting")
        began, begin_command = _act(owner, "begin_srd_spell_cast", **_begin(params))
        assert began.status is ActionStatus.COMMITTED, began.result
        channel = owner.snapshot("casting")
        assert (
            channel.state[f"{SRD}.entities"]["hero"]["extended_casting"]["required_rounds"] == 4800
        )
        assert owner.execute_action(begin_command) == began
        assert owner.snapshot("casting") == channel
    with AuthorityOwner.open(database, "casting-owner") as owner:
        continued, continuation = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=4800,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_5"] == (
            initial.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] - 1
        )
        assert hero["spell_materials"][0]["quantity"] == (
            initial.state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] - 1
        )
        assert owner.execute_action(continuation) == continued
        assert owner.execute_action(command) == cast and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        speech, speech_command = _act(
            owner,
            "communicate_srd_spell",
            listener_state="hero",
            speaker_state="beast",
            language="elvish",
        )
        assert speech.status is ActionStatus.COMMITTED, speech.result
        assert speech.result["context"]["_language_understands"] is True
        checked, check_command = _act(
            owner,
            "test_srd_awakened_intelligence",
            target_state="beast",
            skill="arcana",
            target_number=10,
        )
        assert checked.status is ActionStatus.COMMITTED, checked.result
        assert checked.result["context"]["_awaken_check_modifier"] == 2
        final = owner.snapshot("casting")
        for action, receipt in (
            (speech_command, speech),
            (check_command, checked),
            (command, cast),
        ):
            assert owner.execute_action(action) == receipt and owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
