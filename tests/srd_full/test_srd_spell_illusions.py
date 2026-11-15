"""Illusions consume exact sources, actual scene rays and real Investigation Actions."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_illusion_spells import CAST_CHOICES, PROFILES, extend_runtime, validate_profiles
from scripts.srd_full.srd_reaction_spells import finalize_pending_spell_guards
from scripts.srd_full.srd_spell_illumination import extend_runtime as extend_illumination
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _free, _wall

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _runtime():
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    runtime = _load_runtime(text)
    if (
        set(PROFILES) <= runtime["definitions"]["spells"].keys()
        and ("study_srd_illusion_creature" in runtime["tools"])
        and {p["name"] for p in CAST_CHOICES}
        <= {p["name"] for p in runtime["tools"]["cast_spell"]["parameters"]}
        and "check_srd_illusion_visibility"
        in str(runtime["tools"].get("resolve_srd_illumination_target_sight", {}).get("effects", []))
        and "check_srd_illusion_visibility"
        in str(runtime["tools"].get("perceive_srd_information_proxy", {}).get("effects", []))
    ):
        return runtime, text.encode()
    text = extend_illumination(text)
    text = extend_runtime(text)
    text = refresh_exploration_runtime(text)
    text = finalize_pending_spell_guards(text)
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(PROFILES)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if (
                parameter["name"] == "spell"
                and "enum_values" in parameter
                and "acid_arrow" in parameter["enum_values"]
            ):
                parameter["enum_values"] = sorted(runtime["definitions"]["spells"])
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


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


def _case(spell, **choices):
    state, data, parameters = _information_case(spell)
    _free(state)
    state[f"{SRD}.entities"]["hero"]["speed"] = 60
    data["speed"] = 60
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "illusion-fixture-ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {
                "plane": "material",
                "lighting": choices.get("illusion_lighting", "bright"),
            },
        }
    )
    scene["entities"] += [
        _entity("other", [0, 5, 0], participant_ref="other", creature_type="humanoid")
    ]
    state[f"{SRD}.entities"]["other"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    state[f"{SRD}.entities"]["other"]["name"] = "Other"
    parameters.update(
        illusion_position=[10, 0, 0],
        illusion_size=[5, 5, 5],
        illusion_description="An opaque stone screen",
        illusion_subject_kind="object",
        illusion_trigger="a visible creature",
        illusion_performance_seconds=60,
    )
    parameters.update(choices)
    return state, data, parameters


def _cast(spell, **choices):
    state, data, parameters = _case(spell, **choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = next(reversed(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    return state, data, parameters, source, result


def _grant_other_spell(state, data, spell, **choices):
    """Admit another compatible Wizard spell with its actual carried Material supply."""
    supplied, grant_data, parameters = _case(spell, **choices)
    data["spellcasting_grants"][0]["spells"].append(spell)
    if spell in grant_data.get("wizard_spellbook", []):
        data.setdefault("wizard_spellbook", []).append(spell)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append(spell)
    for supply in supplied[f"{SRD}.entities"]["hero"]["spell_materials"]:
        material = deepcopy(supply)
        material["id"] = spell + "_" + material["id"]
        hero["spell_materials"].append(material)
        hero["inventory"].append(material["inventory_item"])
    parameters["material_item_ids"] = [
        spell + "_" + item for item in parameters["material_item_ids"]
    ]
    return parameters


def _summary(result):
    assert result.result["result_card"] is not None, result.result
    return {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}


def _observe(state, source, observer="hero"):
    return _summary(
        _run("observe_srd_illusion", state, observer_state=observer, illusion_id=source["id"])
    )


@pytest.mark.parametrize("spell", PROFILES)
def test_five_reviewed_records_cast_through_authentic_family(spell):
    declaration = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text()
    )
    validate_profiles({spell: declaration["spells"][spell]["definition"]})
    bad = deepcopy(PROFILES[spell])
    bad["cube_feet"] += 1
    with pytest.raises(SystemExit):
        validate_profiles({spell: bad})
    state, _, _, source, result = _cast(spell)
    assert result.result["status"] == "committed"
    assert source["save_dc"] == 18 and source["installed"]
    proxy = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == source["id"]
    )
    assert (
        proxy["movement"] == "passable"
        and proxy["visibility"] == "clear"
        and proxy["metadata"]["invulnerable"]
    )
    assert source["concentration"] == PROFILES[spell]["concentration"]
    if spell == "mislead":
        assert "invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]


def test_source_cube_range_and_major_sight_fail_atomically():
    for spell, choices, message in [
        ("silent_image", {"illusion_size": [16, 5, 5]}, "Cube"),
        ("minor_illusion", {"illusion_position": [31, 0, 0]}, "range"),
        ("major_image", {"illusion_lighting": "darkness"}, "seen"),
    ]:
        state, data, parameters = _case(spell, **choices)
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match=message):
            _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        assert state == before


@pytest.mark.parametrize("subject_kind", ["object", "creature", "phenomenon", "unqualified"])
def test_minor_image_requires_admitted_object_kind_before_paid_effect(subject_kind):
    state, data, parameters = _case("minor_illusion", illusion_subject_kind=subject_kind)
    before = deepcopy(state)
    if subject_kind != "object":
        with pytest.raises(RulesetPreconditionRejected, match="source-qualified object"):
            _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        assert state == before
        return
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = next(iter(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    assert source["subject_kind"] == "object" and source["installed"]


def test_actual_ray_blocks_other_observer_until_touch_and_preserves_wall_and_blindness():
    state, _, _, source, _ = _cast("silent_image", illusion_size=[10, 10, 5])
    relation = _summary(
        _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    )
    assert relation["Line of sight"] and not relation["Can see"]
    _run("touch_srd_illusion", state, observer_state="hero", illusion_id=source["id"])
    assert _summary(
        _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    )["Can see"]
    assert not _summary(
        _run("resolve_srd_scene_relation", state, actor_state="other", target_state="guard")
    )["Can see"]
    state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    assert not _summary(
        _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    )["Can see"]
    state[f"{SRD}.entities"]["hero"]["conditions"].remove("blinded")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1)]
    assert not _summary(
        _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    )["Can see"]


def test_study_uses_real_d20_saved_dc_and_actual_action(monkeypatch):
    state, data, _, source, _ = _cast("major_image", illusion_position=[5, 0, 0])
    source["save_dc"] = 80
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", action_available=True
    )
    monkeypatch.setattr(dice, "_rng", Random(3))
    monkeypatch.setattr(dice, "set_seed", lambda seed: None)
    result = _summary(
        _run(
            "study_srd_illusion",
            state,
            participant_data=data,
            observer="hero",
            observer_state="hero",
            illusion_id=source["id"],
            illusion_proficiency_factor="none",
        )
    )
    assert not result["Recognized"] and result["Total"] < 80
    assert not state[f"{SRD}.combat"]["action_available"]
    assert "hero" not in source["recognized"]
    state[f"{SRD}.combat"]["action_available"] = True
    state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]["save_dc"] = 0
    assert _summary(
        _run(
            "study_srd_illusion",
            state,
            participant_data=data,
            observer="hero",
            observer_state="hero",
            illusion_id=source["id"],
            illusion_proficiency_factor="none",
        )
    )["Recognized"]
    assert _observe(state, source)["Sensory quality"] == "faint"


def test_higher_slot_major_is_independent_permanent_and_not_free_dismissible():
    state, data, parameters = _case("major_image")
    parameters["slot_resource"] = "spell_slot_4"
    state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] = 2
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = next(iter(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    assert not source["concentration"] and source["expires_tick"] is None
    state[f"{SRD}.entities"]["hero"]["is_alive"] = False
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert source["installed"] and _observe(state, source, observer="guard")["Can see"]
    state[f"{SRD}.entities"]["hero"]["is_alive"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="permanent"):
        _run("dismiss_srd_illusion_spell", state, caster_state="hero", illusion_id=source["id"])
    assert state == before


def test_programmed_trigger_calculated_area_range_performance_and_dormancy():
    state, _, _, source, _ = _cast("programmed_illusion")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    event = scene["entities"][1]
    event["metadata"]["visual_phenomena"] = ["a visible creature"]
    event["position"] = [50, 0, 0]
    with pytest.raises(RulesetPreconditionRejected, match="thirty"):
        _run(
            "trigger_srd_programmed_illusion",
            state,
            illusion_id=source["id"],
            event_subject="guard",
        )
    assert not _observe(state, source)["Can see"]
    event["position"] = [40, 0, 0]
    _run("trigger_srd_programmed_illusion", state, illusion_id=source["id"], event_subject="guard")
    assert _observe(state, source)["Can see"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert not _observe(state, source)["Can see"]
    with pytest.raises(RulesetPreconditionRejected, match="dormancy"):
        _run(
            "trigger_srd_programmed_illusion",
            state,
            illusion_id=source["id"],
            event_subject="guard",
        )
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    _run("trigger_srd_programmed_illusion", state, illusion_id=source["id"], event_subject="guard")
    assert _observe(state, source)["Can see"]


def test_minor_recast_exact_caster_and_real_movement_reveals_only_mover():
    state, data, parameters, source, _ = _cast("minor_illusion")
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 0])
    current = state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
    assert "hero" in current["recognized"] and "guard" not in current["recognized"]
    state = json.loads(json.dumps(state))
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    records = list(state[f"{SRD}.state"]["illusion_spell_sources"].values())
    assert not records[0]["installed"] and records[1]["installed"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert not any(r["installed"] for r in state[f"{SRD}.state"]["illusion_spell_sources"].values())


def test_mislead_current_speed_control_own_invisible_end_double_survives():
    state, _, _, source, _ = _cast("mislead")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["exhaustion_level"] = 2
    with pytest.raises(RulesetPreconditionRejected, match="twice"):
        _run(
            "control_srd_illusion",
            state,
            caster_state="hero",
            illusion_id=source["id"],
            destination=[101, 0, 0],
        )
    _run(
        "control_srd_illusion",
        state,
        caster_state="hero",
        illusion_id=source["id"],
        destination=[100, 0, 0],
        sound="Hello",
    )
    assert "invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run("end_srd_mislead_damage_source", state, caster_state="hero")
    current = state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
    assert (
        "invisible" not in state[f"{SRD}.entities"]["hero"]["conditions"]
        and current["installed"]
        and not current["invisible_active"]
    )
    _run("end_srd_mislead_damage_source", state, caster_state="hero")
    assert source["installed"]
    _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]["installed"]


@pytest.mark.parametrize("event", ["attack", "spell"])
def test_mislead_actual_attack_or_cast_ends_only_invisibility(event):
    state, data, _, source, _ = _cast("mislead")
    if event == "attack":
        result = _run(
            "d20_test",
            state,
            actor_state="hero",
            kind="attack_roll",
            ability="strength",
            ability_modifier=2,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=10,
        )
        assert len(result.result["context"]["_d20_test"]["results"]) == 2
    else:
        _, _, parameters = _case("magic_missile")
        data["spellcasting_grants"][0]["spells"].append("magic_missile")
        data["wizard_spellbook"].append("magic_missile")
        state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("magic_missile")
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    current = state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
    assert current["installed"] and not current["invisible_active"]
    assert "invisible" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert _observe(state, current)["Can see"]


def test_truesight_disbelieves_only_its_observer_and_blindsight_uses_actual_target_range():
    state, _, _, source, _ = _cast("silent_image", illusion_size=[10, 10, 5])
    hero = state[f"{SRD}.entities"]["hero"]
    hero["truesight_feet"] = 120
    assert _summary(
        _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    )["Can see"]
    current = state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
    assert "hero" in current["recognized"] and "other" not in current["recognized"]
    other = state[f"{SRD}.entities"]["other"]
    other["blindsight_feet"] = 30
    assert _summary(
        _run("resolve_srd_scene_relation", state, actor_state="other", target_state="guard")
    )["Can see"]
    current = state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
    assert "other" not in current["recognized"]
    state[f"{SRD}.entities"]["other"]["blindsight_feet"] = 10
    assert not _summary(
        _run("resolve_srd_scene_relation", state, actor_state="other", target_state="guard")
    )["Can see"]


def test_minor_sound_requires_actual_audibility_and_creature_study_reads_saved_skill():
    state, _, _, source, _ = _cast(
        "minor_illusion",
        illusion_minor_mode="sound",
        illusion_sound="A tiger roar",
        illusion_opaque=False,
    )
    assert not _observe(state, source, "guard")["Can hear"]
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["audible_sources"] = [
        source["id"]
    ]
    assert _observe(state, source, "guard")["Can hear"]
    with pytest.raises(RulesetPreconditionRejected, match="physical-interaction"):
        _run("touch_srd_illusion", state, observer_state="guard", illusion_id=source["id"])
    _run(
        "record_srd_creature_skill_checks",
        state,
        target_state="guard",
        skill_checks=[{"skill": "investigation", "modifier": 100}],
    )
    assert _summary(
        _run("study_srd_illusion_creature", state, observer_state="guard", illusion_id=source["id"])
    )["Recognized"]
    assert _observe(state, source, "guard")["Sensory quality"] == "faint"
    state[f"{SRD}.entities"]["guard"]["conditions"].append("deafened")
    assert not _observe(state, source, "guard")["Can hear"]


def test_mislead_double_both_senses_current_sight_and_hearing_after_real_move():
    state, _, _, source, _ = _cast("mislead")
    _run(
        "control_srd_illusion",
        state,
        caster_state="hero",
        illusion_id=source["id"],
        destination=[100, 0, 0],
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    guard = next(e for e in scene["entities"] if e["id"] == "guard")
    guard["position"] = [110, 0, 0]
    guard["metadata"]["audible_to"] = [source["id"]]
    result = _summary(
        _run("perceive_srd_mislead_double", state, caster_state="hero", illusion_id=source["id"])
    )
    assert any("guard" in x for x in result["Visible subjects"])
    assert any("guard" in x for x in result["Heard subjects"])
    state[f"{SRD}.entities"]["hero"]["conditions"] += ["blinded", "deafened"]
    result = _summary(
        _run("perceive_srd_mislead_double", state, caster_state="hero", illusion_id=source["id"])
    )
    assert not result["Visible subjects"] and not result["Heard subjects"]


def test_model_study_derives_actual_actor_and_skill_from_selected_participant():
    from types import SimpleNamespace

    from sylvia_gm.agent._provider_session import _derive_mechanics_parameters

    runtime, _ = _runtime()
    schema = _tool_schema(runtime["tools"]["study_srd_illusion"], tool_name="study_srd_illusion")
    derivations = {
        key: child["x-sylvia-gm-derived"]
        for key, child in schema["properties"].items()
        if "x-sylvia-gm-derived" in child
    }
    actual = _derive_mechanics_parameters(
        {"observer": "hero", "illusion_id": "selected"},
        derivations,
        [SimpleNamespace(participant_id="hero", data={"skill_proficiencies": ["investigation"]})],
    )
    assert actual["observer_state"] == "hero"
    assert (
        actual["illusion_skill"] == "investigation"
        and actual["illusion_proficiency_factor"] == "normal"
    )
    assert "target_number" not in schema["properties"]


@pytest.mark.parametrize(
    "variation,visible",
    [
        ("image", False),
        ("recognized", True),
        ("wall", False),
        ("blinded", False),
        ("truesight", True),
        ("blindsight_near", True),
        ("blindsight_far", False),
    ],
)
def test_private_target_and_point_sight_use_exact_observer_image_visibility(variation, visible):
    state, _, _, source, _ = _cast("silent_image", illusion_size=[10, 10, 5])
    if variation in {"recognized", "wall", "blinded"}:
        _run("touch_srd_illusion", state, observer_state="hero", illusion_id=source["id"])
    hero = state[f"{SRD}.entities"]["hero"]
    if variation == "wall":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(_wall(3))
    if variation == "blinded":
        hero["conditions"].append("blinded")
    if variation == "truesight":
        hero["truesight_feet"] = 120
    if variation.startswith("blindsight"):
        hero["blindsight_feet"] = 30 if variation == "blindsight_near" else 10
    target = _run(
        "resolve_srd_illumination_target_sight",
        state,
        observer_state="hero",
        target_state="guard",
    ).result["context"]
    assert target["_illumination_target_can_see"] is visible
    point = _run(
        "resolve_srd_illumination_point_sight", state, observer_state="hero", point=[20, 0, 0]
    ).result["context"]
    assert point["_illumination_point_can_see"] is visible
    reaction = _run(
        "resolve_srd_opportunity_perception", state, observer_state="hero", target_state="guard"
    ).result["context"]
    assert reaction["_observer_can_see_target"] is visible


def test_major_seen_origin_uses_existing_point_sight_through_another_image():
    state, data, parameters = _case("major_image", illusion_position=[20, 0, 0])
    supplied, _, minor = _case("minor_illusion", illusion_position=[10, 0, 0])
    data["spellcasting_grants"][0]["spells"].append("minor_illusion")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("minor_illusion")
    for supply in supplied[f"{SRD}.entities"]["hero"]["spell_materials"]:
        material = deepcopy(supply)
        material["id"] = "minor_" + material["id"]
        hero["spell_materials"].append(material)
        hero["inventory"].append(material["inventory_item"])
    minor["material_item_ids"] = ["minor_" + item for item in minor["material_item_ids"]]
    _run("cast_spell_outside_combat", state, participant_data=data, **minor)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="origin must be seen"):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 120
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    records = state[f"{SRD}.state"]["illusion_spell_sources"].values()
    assert any(r["installed"] and r["spell"] == "major_image" for r in records)


def test_actual_ethereal_projection_checks_images_in_qualified_material_plane():
    state, data, _, source, _ = _cast("minor_illusion")
    _, _, ethereal = _case("etherealness")
    data["spellcasting_grants"][0]["spells"].append("etherealness")
    data.setdefault("wizard_spellbook", []).append("etherealness")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("etherealness")
    _run("cast_spell_outside_combat", state, participant_data=data, **ethereal)
    result = _run(
        "resolve_srd_illumination_target_sight",
        state,
        observer_state="hero",
        target_state="guard",
    ).result["context"]
    assert result["_illumination_target_projected"]
    assert result["_illumination_target_observer_plane"] == "ethereal"
    assert result["_illumination_target_plane"] == "material"
    assert not result["_illumination_target_can_see"]
    point = _run(
        "resolve_srd_illumination_point_sight",
        state,
        observer_state="hero",
        point=[20, 0, 0],
        perception_plane="material",
    ).result["context"]
    assert not point["_illumination_point_can_see"]
    state[f"{SRD}.entities"]["hero"]["truesight_feet"] = 120
    result = _run(
        "resolve_srd_opportunity_perception",
        state,
        observer_state="hero",
        target_state="guard",
    ).result["context"]
    assert result["_observer_can_see_target"]
    current = state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]
    assert current["recognized"] == ["hero"]
    guard = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard"
    )
    guard["position"] = [61, 0, 0]
    assert not _run(
        "resolve_srd_illumination_target_sight", state, observer_state="hero", target_state="guard"
    ).result["context"]["_illumination_target_can_see"]


@pytest.mark.parametrize("spell", ["mislead", "arcane_eye"])
def test_actual_sensor_sees_image_itself_and_obeys_own_or_caster_vision(spell):
    state, data, _, image, _ = _cast("minor_illusion")
    parameters = _grant_other_spell(state, data, spell)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    records = state[f"{SRD}.state"]["illusion_spell_sources"].values()
    double = next((r for r in records if r["spell"] == "mislead"), None)

    def view():
        if double:
            return _summary(
                _run(
                    "perceive_srd_mislead_double",
                    state,
                    caster_state="hero",
                    illusion_id=double["id"],
                )
            )["Visible subjects"]
        return _summary(
            _run("query_srd_information_spell", state, caster_state="hero", spell=spell)
        )["Information"]

    assert image["id"] + ": seeing" in view()
    assert "guard: seeing" not in view()
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(truesight_feet=120, blindsight_feet=120)
    if spell == "mislead":
        assert "guard: seeing" in view()
    else:
        hero["conditions"].append("blinded")
        assert "guard: seeing" not in view()
        current = state[f"{SRD}.state"]["illusion_spell_sources"][image["id"]]
        assert "hero" not in current["recognized"]
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 0])
        _run("touch_srd_illusion", state, observer_state="hero", illusion_id=image["id"])
        assert "guard: seeing" in view()


@pytest.mark.parametrize("spell", ["minor_illusion", "programmed_illusion"])
def test_actual_eye_excludes_sound_and_dormant_image_then_consumes_performance_clock(spell):
    choices = (
        {"illusion_minor_mode": "sound", "illusion_sound": "A tiger roar", "illusion_opaque": False}
        if spell == "minor_illusion"
        else {}
    )
    state, data, _, image, _ = _cast(spell, **choices)
    parameters = _grant_other_spell(state, data, "arcane_eye")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)

    def view():
        return _summary(
            _run("query_srd_information_spell", state, caster_state="hero", spell="arcane_eye")
        )["Information"]

    assert image["id"] + ": seeing" not in view()
    assert "guard: seeing" in view()
    if spell == "programmed_illusion":
        guard = next(
            entity
            for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if entity["id"] == "guard"
        )
        guard["metadata"]["visual_phenomena"] = ["a visible creature"]
        _run(
            "trigger_srd_programmed_illusion", state, illusion_id=image["id"], event_subject="guard"
        )
        assert image["id"] + ": seeing" in view()
        assert "guard: seeing" not in view()
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
        assert image["id"] + ": seeing" not in view()
        assert "guard: seeing" in view()


def test_public_image_study_reopens_retries_and_later_visibility_consumes_recognition(
    tmp_path, monkeypatch
):
    from sylvia_gm.agent._provider_session import _derive_mechanics_parameters
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(
        public, "_spell_case", lambda spell: _case(spell, illusion_position=[5, 0, 0])
    )
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda seed: None)
    database = tmp_path / "image-study.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "silent_image",
            participant_overrides={
                "abilities": {
                    "strength": 10,
                    "dexterity": 10,
                    "constitution": 10,
                    "intelligence": 20,
                    "wisdom": 10,
                    "charisma": 10,
                }
            },
        )
        # Study derives Intelligence from the admitted participant.
        receipt, cast_command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        source = next(iter(saved.state[f"{SRD}.state"]["illusion_spell_sources"].values()))
        mechanics = StatelessMcpMechanics(owner)
        names = {tool.name for tool in mechanics.list_tools("casting")}
        assert {"observe_srd_illusion", "study_srd_illusion", "control_srd_illusion"} <= names
        blocked, _ = _act(
            owner, "resolve_srd_scene_relation", actor_state="hero", target_state="guard"
        )
        assert not _summary(blocked)["Can see"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(cast_command) == receipt
        runtime, _ = _runtime()
        schema = _tool_schema(
            runtime["tools"]["study_srd_illusion"], tool_name="study_srd_illusion"
        )
        derivations = {
            key: child["x-sylvia-gm-derived"]
            for key, child in schema["properties"].items()
            if "x-sylvia-gm-derived" in child
        }
        # The public adapter derives mechanics from the selected participant and source.
        actual = _derive_mechanics_parameters(
            {"observer": "hero", "illusion_id": source["id"]},
            derivations,
            owner.snapshot("casting").participants,
        )
        studied, study_command = _act(owner, "study_srd_illusion", **actual)
        assert studied.status is ActionStatus.COMMITTED and _summary(studied)["Recognized"]
        recognized = owner.snapshot("casting")
        assert mechanics.execute_action(study_command) == studied
        assert owner.snapshot("casting").state == recognized.state
        visible, _ = _act(
            owner, "resolve_srd_scene_relation", actor_state="hero", target_state="guard"
        )
        assert _summary(visible)["Can see"]
        _act(owner, "advance_srd_lasting_time", elapsed_rounds=100)
        ended = owner.snapshot("casting")
        assert not ended.state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]["installed"]
        assert ended.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
