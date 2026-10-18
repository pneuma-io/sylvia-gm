"""Information spells prove their source-specific later consumers against the admitted scene."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_information_spells import PROFILES, validate_profiles

from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_extended_casting import _begin
from tests.test_srd_spell_result_cards import SRD, _spell_case

ROOT = Path(__file__).resolve().parents[1]


def _entity(subject, point, **metadata):
    return {
        "id": subject,
        "map_id": "information",
        "position": point,
        "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
        "height": 6,
        "facing": [1, 0],
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"plane": "material", "lighting": "bright", **metadata},
    }


def _scene(*entities, geometry=None, zones=None):
    return {
        "format": 1,
        "map": {
            "id": "information",
            "revision": 0,
            "bounds": {"min": [-30000, -30000, -30000], "max": [30000, 30000, 30000]},
            "grid": {"cell_size": 100, "unit": "ft", "origin": [-30000, -30000]},
            "layers": [{"id": "world", "min_elevation": -30000, "max_elevation": 30000}],
        },
        "entities": list(entities),
        "geometry": geometry or [],
        "zones": zones or [],
        "terrain": [],
    }


def _wall(thickness, material="stone", low_y=-10, high_y=10, kind="box"):
    volume = {"kind": "box", "min": [10, low_y, -10], "max": [10 + thickness, high_y, 10]}
    if kind == "cylinder":
        volume = {
            "kind": "cylinder",
            "center": [10 + thickness * 0.5, 0, -10],
            "radius": thickness * 0.5,
            "height": 20,
        }
    return {
        "id": "wall",
        "map_id": "information",
        "volume": volume,
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"material": material},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
    }


def _case(spell, **choices):
    state, data, params = _spell_case(spell)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
            _entity(
                "guard",
                [20, 0, 0],
                participant_ref="guard",
                creature_type="fiend",
                magical_effect=True,
                spell_school="abjuration",
                poison=True,
                detected_kind="serpent venom",
                kind="serpent",
            ),
            _entity("orb", [25, 0, 0], is_object=True, kind="jewel", magical_effect=True),
        ),
    }
    params.update(
        information_kind="serpent",
        information_familiar=True,
        information_seen_close=True,
        information_obvious_place=True,
        information_position=[5, 0, 0],
        information_lighting="bright",
    )
    state[f"{SRD}.entities"]["hero"]["languages"] = ["common"]
    state[f"{SRD}.entities"]["guard"]["languages"] = ["infernal"]
    if spell == "tongues":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [5, 0, 0]
    if spell == "locate_object":
        params["information_kind"] = "jewel"
    if spell == "locate_animals_or_plants":
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["creature_type"] = "beast"
    if spell == "clairvoyance":
        state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["components"].append(
            "clairvoyance.glass_eye"
        )
    params.update(choices)
    return state, data, params


def _free(state):
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, active_actor=None)


def _cast(spell, **choices):
    state, data, params = _case(spell, **choices)
    if spell == "clairvoyance":
        _free(state)
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        _run(
            "continue_srd_spell_cast",
            state,
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    _run("cast_spell", state, participant_data=data, **params)
    return state, data, params


def _summary(result):
    return {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}


def _query(state, spell, **choices):
    return _summary(
        _run("query_srd_information_spell", state, caster_state="hero", spell=spell, **choices)
    )


@pytest.mark.parametrize("spell", PROFILES)
def test_exact_source_profiles_cast_through_the_actual_family(spell):
    declaration = yaml.safe_load(
        (ROOT / "rulesets/srd-lite/content/spell-mechanics.yaml").read_text()
    )
    definition = declaration["spells"][spell]["definition"]
    validate_profiles({spell: definition})
    bad = deepcopy(definition)
    bad["query_radius"] += 1
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles({spell: bad})
    state, _, _ = _cast(spell)
    records = state[f"{SRD}.state"]["turn_effects"]
    if definition["effect_rounds"]:
        assert records[-1]["kind"] == "information_spell"
        assert (
            records[-1]["expires_tick"] - records[-1]["started_tick"]
            == definition["effect_rounds"] * 2
        )
    else:
        assert records == []
    source = json.loads(
        (ROOT / "rulesets/srd-lite/content/spell-mechanics-source.json").read_text()
    )["spells"][spell]
    assert source["source_unit_id"].endswith(spell.replace("_", "-"))


@pytest.mark.parametrize(
    "material,threshold",
    [("stone", 1), ("dirt", 1), ("wood", 1), ("metal", 1 / 12), ("lead", 0.001)],
)
@pytest.mark.parametrize(
    "spell", ["detect_magic", "detect_evil_and_good", "detect_poison_and_disease"]
)
def test_detect_source_material_thresholds_and_later_presence(
    spell, material, threshold, monkeypatch
):
    state, _, _ = _cast(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"] = [_wall(threshold, material)]
    import sylvia_gm.application.package_runtime as runtime

    actual = runtime.execute_tool
    blocked = []

    def observe(*args, **kwargs):
        result = actual(*args, **kwargs)
        if args[0] == "srd_information_barriers" and args[1]["end"] == [20, 0, 0]:
            blocked.append(result.context["_information_blocked"])
        return result

    monkeypatch.setattr(runtime, "execute_tool", observe)
    result = _query(state, spell)
    assert blocked[-1] is True
    if spell != "detect_magic":
        assert result["Present"] is False
    if material != "lead":
        scene["geometry"] = [_wall(threshold * 0.999, material)]
        assert _query(state, spell)["Present"] is True
        assert blocked[-1] is False


def test_scene_relation_calculates_range_direction_sight_and_actual_plane():
    state, _, _ = _case("detect_magic")
    result = _summary(
        _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    )
    assert result["Distance"] == 20
    assert result["Direction"] == [1, 0, 0]
    assert result["Line of sight"] is True
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "ethereal"
    assert (
        _summary(
            _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
        )["Same plane"]
        is False
    )


def test_locate_object_calculates_nearest_direction_and_motion_without_revealing_distance():
    state, _, _ = _cast("locate_object")
    result = _query(state, "locate_object")
    assert result["Direction"] == [1, 0, 0]
    assert result["Distance"] is None
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][2].update(position=[0, -15, 0])
    scene["entities"][2]["metadata"].update(previous_position=[0, -10, 0], moving=True)
    assert _query(state, "locate_object")["Movement direction"] == [0, -1, 0]
    scene["entities"].append(_entity("nearer", [-5, 0, 0], is_object=True, kind="jewel"))
    assert _query(state, "locate_object")["Direction"] == [-1, 0, 0]


def _zone(identifier, low, high, **metadata):
    return {
        "id": identifier,
        "map_id": "information",
        "shape": {"kind": "box", "min": low, "max": high},
        "metadata": {"plane": "material", **metadata},
    }


def _reject(tool, state, **params):
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        _run(tool, state, **params)
    assert state == before


def test_detect_locations_include_hallow_areas_and_magical_contagions_not_ordinary_disease():
    state, _, _ = _cast("detect_evil_and_good")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"]["creature_type"] = "humanoid"
    scene["zones"] = [_zone("hallowed", [15, -2, -2], [50, 2, 2], hallow=True)]
    assert _query(state, "detect_evil_and_good")["Information"] == ["hallow at 15, 0, 0 feet"]
    state, _, _ = _cast("detect_poison_and_disease")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"].update(poison=False, disease=True)
    assert _query(state, "detect_poison_and_disease")["Present"] is False
    scene["entities"][1]["metadata"].update(magical_contagion=True, detected_kind="magical rot")
    assert _query(state, "detect_poison_and_disease")["Information"] == [
        "magical rot at 20, 0, 0 feet"
    ]


@pytest.mark.parametrize(
    "creature_type", ["aberration", "celestial", "elemental", "fey", "fiend", "undead"]
)
def test_detect_evil_good_all_source_types_and_exact_range(creature_type):
    state, _, _ = _cast("detect_evil_and_good")
    guard = state[f"{SRD}.state"]["spatial_scene"]["entities"][1]
    guard["metadata"]["creature_type"] = creature_type
    guard["position"] = [30, 0, 0]
    assert _query(state, "detect_evil_and_good")["Present"] is True
    guard["position"] = [30.001, 0, 0]
    assert _query(state, "detect_evil_and_good")["Present"] is False


def test_find_traps_returns_only_general_danger_for_purposeful_physical_traps():
    state, data, params = _case("find_traps")
    trap = _wall(1)
    trap.update(
        id="secret_pressure_trap", volume={"kind": "box", "min": [119, -1, -1], "max": [130, 1, 1]}
    )
    trap["metadata"].update(plane="material", intentional_trap=True, general_danger="crushing")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [trap]
    result = _run("cast_spell", state, participant_data=data, **params)
    details = result.result["result_card"]["details"]
    assert {r["label"]: r["value"] for r in details}["General danger"] == ["crushing"]
    assert "secret_pressure_trap" not in str(details)
    assert "119" not in str(details)
    state, data, params = _case("find_traps")
    trap["metadata"]["intentional_trap"] = False
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [trap]
    result = _run("cast_spell", state, participant_data=data, **params)
    assert {r["label"]: r["value"] for r in result.result["result_card"]["details"]}[
        "Trap present"
    ] is False


def test_detect_magic_actual_saved_effect_school_and_magic_action_cost():
    state, data, _ = _cast("detect_magic")
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"].update(magical_effect=False)
    _run(
        "install_srd_timed_senses",
        state,
        spell="darkvision",
        caster_state="guard",
        target_state="guard",
    )
    state[f"{SRD}.combat"].update(action_available=True, normal_action_spent=False)
    result = _query(state, "detect_magic", mode="aura")
    assert "guard: faint aura; spell schools transmutation" in result["Information"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    _reject(
        "query_srd_information_spell", state, caster_state="hero", spell="detect_magic", mode="aura"
    )
    _free(state)
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    assert _query(state, "detect_magic", mode="aura")["Information"] == []


def _mind_blank(state, subject="guard"):
    state[f"{SRD}.entities"][subject]["has_timed_actor_defenses"] = True
    state[f"{SRD}.state"]["turn_effects"].append(
        {
            "kind": "timed_actor_defense",
            "spell": "mind_blank",
            "caster": subject,
            "target": subject,
            "started_tick": 0,
            "expires_tick": 28800,
            "concentration": False,
        }
    )


def test_mind_blank_filters_location_before_nearest_choice_and_does_not_protect_objects():
    state, _, _ = _cast("locate_creature")
    _mind_blank(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(
        _entity(
            "other", [0, 30, 0], participant_ref="other", creature_type="humanoid", kind="serpent"
        )
    )
    assert _query(state, "locate_creature")["Direction"] == [0, 1, 0]
    state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["psychic"]
    state[f"{SRD}.state"]["turn_effects"] = [
        r for r in state[f"{SRD}.state"]["turn_effects"] if r["spell"] != "mind_blank"
    ]
    assert _query(state, "locate_creature")["Direction"] == [1, 0, 0]
    state, _, _ = _cast("locate_object")
    _mind_blank(state)
    assert _query(state, "locate_object")["Direction"] == [1, 0, 0]


def test_locate_creature_changed_form_lead_and_range_are_later_consumers():
    state, _, _ = _cast("locate_creature")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"]["different_form"] = True
    assert _query(state, "locate_creature")["Present"] is False
    scene["entities"][1]["metadata"]["different_form"] = False
    scene["geometry"] = [_wall(0.000001, "lead")]
    assert _query(state, "locate_creature")["Present"] is False
    scene["geometry"] = []
    scene["entities"][1]["position"] = [1000, 0, 0]
    assert _query(state, "locate_creature")["Present"] is True
    scene["entities"][1]["position"] = [1000.01, 0, 0]
    assert _query(state, "locate_creature")["Present"] is False


def test_arcane_eye_has_own_all_direction_darkvision_and_actual_bonus_action_movement():
    state, _, _ = _cast("arcane_eye")
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1].update(position=[-25, 0, 0])
    scene["entities"][1]["metadata"]["lighting"] = "darkness"
    assert "guard: seeing" in _query(state, "arcane_eye")["Information"]
    scene["entities"][1]["position"] = [-25.01, 0, 0]
    assert "guard: seeing" not in _query(state, "arcane_eye")["Information"]
    _run(
        "operate_srd_information_proxy",
        state,
        caster_state="hero",
        spell="arcane_eye",
        destination=[5, 10, 0],
    )
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    assert scene is not state[f"{SRD}.state"]["spatial_scene"]
    proxy = next(
        e
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if e["id"] == "hero:arcane_eye"
    )
    assert proxy["position"] == [5, 10, 0]
    _reject(
        "operate_srd_information_proxy",
        state,
        caster_state="hero",
        spell="arcane_eye",
        destination=[5, 11, 0],
    )


@pytest.mark.parametrize("gap,allowed", [(1 / 12, True), (0.999 / 12, False)])
def test_arcane_eye_one_inch_opening_is_calculated(gap, allowed):
    state, _, _ = _cast("arcane_eye")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    walls = [_wall(1, low_y=-10, high_y=-gap / 2), _wall(1, low_y=gap / 2, high_y=10)]
    walls[1]["id"] = "upper"
    scene["geometry"] = walls
    if allowed:
        _run(
            "operate_srd_information_proxy",
            state,
            caster_state="hero",
            spell="arcane_eye",
            destination=[15, 0, 0],
        )
    else:
        _reject(
            "operate_srd_information_proxy",
            state,
            caster_state="hero",
            spell="arcane_eye",
            destination=[15, 0, 0],
        )


def test_clairvoyance_switches_at_fixed_position_and_hearing_requires_actual_audibility():
    state, _, _ = _cast("clairvoyance")
    _run(
        "operate_srd_information_proxy",
        state,
        caster_state="hero",
        spell="clairvoyance",
        modality="hearing",
    )

    assert _query(state, "clairvoyance")["Information"] == []
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["audible_to"] = [
        "hero:clairvoyance"
    ]
    assert _query(state, "clairvoyance")["Information"] == ["guard: hearing"]
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["deafened"]
    assert _query(state, "clairvoyance")["Information"] == []
    state[f"{SRD}.entities"]["hero"]["conditions"] = []
    _mind_blank(state)
    assert _query(state, "clairvoyance")["Information"] == []
    _run("dismiss_srd_information_spell", state, caster_state="hero", spell="clairvoyance")
    proxy = next(
        e
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if e["id"] == "hero:clairvoyance"
    )
    assert proxy["position"] == [5, 0, 0] and proxy["active"] is False


def test_active_source_light_is_calculated_at_later_observation_and_respects_obstruction():
    state, _, _ = _case("detect_magic")
    state[f"{SRD}.entities"]["guard"]["has_actor_spell_modifiers"] = True
    lasting = state[f"{SRD}.state"]
    lasting["turn_effects"].append(
        {
            "kind": "actor_spell_modifier",
            "caster": "guard",
            "target": "guard",
            "spell": "fire_shield",
            "mode": "fire_shield_warm",
            "started_tick": 0,
            "expires_tick": 100,
            "concentration": False,
        }
    )

    def light(point, plane="material"):
        return _run(
            "resolve_srd_information_light",
            state,
            point=point,
            plane=plane,
            ambient="darkness",
        ).result["context"]["_information_light"]

    assert light([10, 0, 0]) == "bright"
    assert light([0, 0, 0]) == "dim"
    assert light([-0.01, 0, 0]) == "darkness"
    assert light([10, 0, 0], "ethereal") == "darkness"
    lasting["spatial_scene"]["geometry"] = [_wall(0.1)]
    assert light([0, 0, 0]) == "darkness"
    lasting["spatial_scene"]["geometry"] = []
    lasting["lasting_tick"] = 100
    assert light([10, 0, 0]) == "darkness"


def test_plant_questions_filter_actual_event_positions_age_and_mind_blank():
    state, _, _ = _cast("speak_with_plants")
    state[f"{SRD}.state"]["lasting_tick"] = 2
    events = [
        {"position": [30, 0, 0], "tick": 0, "description": "a cart passed"},
        {"position": [30.01, 0, 0], "tick": 0, "description": "outside the area"},
        {"position": [0, 0, 0], "tick": -28800, "description": "older than a day"},
        {
            "position": [0, 0, 0],
            "tick": 1,
            "description": "the protected traveler",
            "subject_ref": "guard",
        },
    ]
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("tree", [10, 0, 0], is_plant=True, plant_events=events)
    )
    _mind_blank(state)
    result = _summary(
        _run(
            "command_srd_plants",
            state,
            caster_state="hero",
            command="question",
            plant_id="tree",
        )
    )
    assert result["Past-day events in the area"] == ["a cart passed"]


def test_animals_influence_uses_derived_pc_and_saved_npc_skills_in_exploration():
    state, data, _ = _cast("speak_with_animals")
    _free(state)
    entities = state[f"{SRD}.entities"]
    entities["guard"]["creature_type"] = "beast"
    result = _summary(
        _run(
            "communicate_srd_spell",
            state,
            participant_data=data,
            listener_state="hero",
            speaker_state="guard",
            purpose="influence",
            actor="hero",
            influence_skill="performance",
        )
    )
    assert result["Influence outcome"] in {"success", "failure"}
    entities["hero"]["creature_kind"] = "stat_block"
    entities["hero"]["skill_check_modifiers"] = {"performance": 100}
    result = _summary(
        _run(
            "communicate_srd_spell",
            state,
            listener_state="hero",
            speaker_state="guard",
            purpose="influence",
            influence_skill="performance",
            target_number=10,
        )
    )
    assert result["Influence outcome"] == "success"
    assert result["Influence total"] >= 101


def test_ritual_information_cast_uses_actual_interval_and_leaves_slots_unspent():
    state, data, params = _case("detect_poison_and_disease")
    _free(state)
    params.update(casting_mode="ritual", slot_resource="ritual")
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.state"]["lasting_tick"] == 200
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert _query(state, "detect_poison_and_disease")["Present"] is True


def test_arcane_eye_clearance_uses_rounded_solid_corners():
    state, _, _ = _cast("arcane_eye")
    volume = {"kind": "box", "min": [10, 0.04, 0.04], "max": [11, 1, 1]}
    barrier = _wall(1)
    barrier["volume"] = volume
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [barrier]
    # Inflated bounds overlap, but Euclidean clearance exceeds the eye radius.
    _run(
        "operate_srd_information_proxy",
        state,
        caster_state="hero",
        spell="arcane_eye",
        destination=[15, 0, 0],
    )


def test_hallow_cylinder_uses_its_actual_nearest_surface_at_the_fixed_range():
    state, _, _ = _cast("detect_evil_and_good")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["metadata"]["creature_type"] = "humanoid"
    scene["zones"] = [
        {
            "id": "hallow",
            "map_id": "information",
            "shape": {"kind": "cylinder", "center": [35, 0, -5], "radius": 5, "height": 10},
            "metadata": {"plane": "material", "hallow": True},
        }
    ]
    assert _query(state, "detect_evil_and_good")["Present"] is True
    scene["zones"][0]["shape"]["center"][0] = 35.01
    assert _query(state, "detect_evil_and_good")["Present"] is False


def test_partial_trap_visibility_is_not_reduced_to_its_hidden_nearest_point():
    state, data, params = _case("find_traps")
    wall = _wall(1, low_y=-0.2, high_y=0.2)
    trap = _wall(2)
    trap.update(id="trap", volume={"kind": "box", "min": [20, -5, 0], "max": [22, 5, 3]})
    trap["metadata"].update(
        intentional_trap=True,
        general_danger="falling blade",
        plane="material",
        lighting="bright",
    )
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall, trap]
    result = _run("cast_spell", state, participant_data=data, **params)
    assert "falling blade" in str(result.result["result_card"]["details"])


def test_tongues_incoming_signed_and_outgoing_language_use_actual_perception():
    state, _, _ = _cast("tongues")
    result = _summary(
        _run(
            "communicate_srd_spell",
            state,
            listener_state="guard",
            speaker_state="hero",
            channel="signs",
        )
    )
    assert result["Understands"] is True
    assert (
        _summary(
            _run("communicate_srd_spell", state, listener_state="hero", speaker_state="guard")
        )["Understands"]
        is True
    )
    state[f"{SRD}.entities"]["hero"]["languages"] = []
    _reject("communicate_srd_spell", state, listener_state="hero", speaker_state="guard")
    state[f"{SRD}.entities"]["hero"]["languages"] = ["common"]
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["blinded"]
    _reject(
        "communicate_srd_spell",
        state,
        listener_state="guard",
        speaker_state="hero",
        channel="signs",
    )


def test_speak_animals_gives_actual_influence_skill_action_and_bounded_information():
    state, data, _ = _cast("speak_with_animals")
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "beast"
    state[f"{SRD}.combat"].update(action_available=True, normal_action_spent=False)
    result = _summary(
        _run(
            "communicate_srd_spell",
            state,
            participant_data=data,
            listener_state="hero",
            speaker_state="guard",
            purpose="influence",
            actor="hero",
            influence_skill="performance",
        )
    )
    assert result["All Influence skills"] is True
    assert result["Influence outcome"] in {"success", "failure"}
    assert state[f"{SRD}.combat"]["action_available"] is False
    _reject(
        "communicate_srd_spell",
        state,
        listener_state="hero",
        speaker_state="guard",
        purpose="past_day",
        hours_ago=24.01,
    )


def test_plants_terrain_consumes_actual_exploration_movement_until_source_dismissal():
    state, _, _ = _cast("speak_with_plants")
    _free(state)
    state[f"{SRD}.state"]["spatial_scene"]["zones"] = [
        _zone("growth", [-5, -5, -5], [50, 5, 5], plants=True)
    ]
    _run("command_srd_plants", state, caster_state="hero", command="difficult_terrain")
    result = _summary(
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[10, 0, 0])
    )
    assert result["Movement spent"] == 20
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 20
    _reject("move_srd_scene_creature", state, actor_state="hero", destination=[20, 0, 0])
    _run("dismiss_srd_information_spell", state, caster_state="hero", spell="speak_with_plants")
    result = _summary(
        _run("move_srd_scene_creature", state, actor_state="hero", destination=[20, 0, 0])
    )
    assert result["Movement spent"] == 10
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[25, 0, 0])
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 5


def test_plant_part_motion_changes_the_admitted_scene_without_uprooting_parent():
    state, _, _ = _cast("speak_with_plants")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] += [
        _entity("tree", [10, 10, 0], nonmagical_plant=True),
        _entity("branch", [10, 10, 5], plant_owner="tree", plant_part="branch", reach_feet=10),
    ]
    _run(
        "command_srd_plants",
        state,
        caster_state="hero",
        command="move_branches",
        plant_id="tree",
        plant_part_id="branch",
        destination=[10, 15, 5],
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    assert next(e for e in scene["entities"] if e["id"] == "branch")["position"] == [10, 15, 5]
    assert next(e for e in scene["entities"] if e["id"] == "tree")["position"] == [10, 10, 0]
    _reject("command_srd_plants", state, caster_state="hero", command="uproot", plant_id="tree")
    _reject(
        "command_srd_plants",
        state,
        caster_state="hero",
        command="move_branches",
        plant_id="tree",
        plant_part_id="branch",
        destination=[100, 100, 100],
    )


@pytest.mark.parametrize(
    "spell,later,choices,label,expected",
    [
        ("arcane_eye", "query_srd_information_spell", {"spell": "arcane_eye"}, "Present", True),
        (
            "locate_object",
            "query_srd_information_spell",
            {"spell": "locate_object"},
            "Direction",
            [1, 0, 0],
        ),
        (
            "tongues",
            "communicate_srd_spell",
            {"listener_state": "guard", "speaker_state": "hero"},
            "Understands",
            True,
        ),
        (
            "speak_with_plants",
            "command_srd_plants",
            {"command": "difficult_terrain"},
            "Information available",
            True,
        ),
    ],
)
def test_public_information_cast_reopens_retries_then_consumes_saved_source(
    tmp_path,
    monkeypatch,
    spell,
    later,
    choices,
    label,
    expected,
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(public, "_spell_case", _case)
    database = tmp_path / f"{spell}.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, spell)
        mechanics = StatelessMcpMechanics(owner)
        advertised = mechanics.list_tools("casting")
        assert later in {tool.name for tool in advertised}
        schema = next(tool.input_schema for tool in advertised if tool.name == later)
        assert "lasting_state" not in schema["properties"]
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        mechanics = StatelessMcpMechanics(reopened)
        assert reopened.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command) == receipt
        if later != "communicate_srd_spell":
            choices = choices | {"caster_state": "hero"}
        consequence, later_command = _act(reopened, later, **choices)
        assert consequence.status is ActionStatus.COMMITTED
        summary = {
            row["label"]: row["value"] for row in consequence.result["result_card"]["summary"]
        }
        assert summary[label] == expected
        after = reopened.snapshot("casting")
        assert mechanics.execute_action(later_command) == consequence
        assert reopened.snapshot("casting").state == after.state


def test_actual_language_choice_does_not_infer_a_multilingual_utterance():
    state, _, _ = _case("tongues")
    entities = state[f"{SRD}.entities"]
    entities["hero"]["languages"] = ["common"]
    entities["guard"]["languages"] = ["common", "infernal"]
    _reject("communicate_srd_spell", state, listener_state="hero", speaker_state="guard")
    _reject(
        "communicate_srd_spell",
        state,
        listener_state="hero",
        speaker_state="guard",
        language="infernal",
    )
    assert (
        _summary(
            _run(
                "communicate_srd_spell",
                state,
                listener_state="hero",
                speaker_state="guard",
                language="common",
            )
        )["Understands"]
        is True
    )


@pytest.mark.parametrize("obstruction", ["distance", "plane", "solid"])
def test_tongues_cannot_cast_using_a_forged_contact_fact(obstruction):
    state, data, params = _case("tongues")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if obstruction == "distance":
        scene["entities"][1]["position"] = [5.01, 0, 0]
    elif obstruction == "plane":
        scene["entities"][1]["metadata"]["plane"] = "ethereal"
    else:
        wall = _wall(0.1)
        wall["volume"].update(min=[2, -10, -10], max=[2.1, 10, 10])
        scene["geometry"] = [wall]
    with pytest.raises(RulesetPreconditionRejected, match="actual same-plane contact"):
        _run("cast_spell", state, participant_data=data, **params)


def test_information_search_rejects_an_unrepresented_part_of_its_fixed_radius():
    state, data, params = _case("locate_animals_or_plants")
    # This instantaneous spell must account for its entire five-mile search sphere.
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["map"]["bounds"] = {"min": [-100, -100, -100], "max": [100, 100, 100]}
    scene["map"]["grid"]["origin"] = [-100, -100]
    scene["map"]["layers"] = [{"id": "world", "min_elevation": -100, "max_elevation": 100}]
    state[f"{SRD}.combat"].update(action_available=True, normal_action_spent=False)
    with pytest.raises(RulesetPreconditionRejected, match="complete search radius"):
        _run("cast_spell", state, participant_data=data, **params)


def test_clairvoyance_cannot_relocate_its_fixed_sensor():
    state, _, _ = _cast("clairvoyance")
    _reject(
        "operate_srd_information_proxy",
        state,
        caster_state="hero",
        spell="clairvoyance",
        destination=[10, 0, 0],
        modality="hearing",
    )


def test_sensor_observation_consumes_invisibility_native_senses_and_actual_plane():
    state, _, _ = _cast("clairvoyance")

    def observe():
        return _summary(
            _run(
                "observe_srd_information_proxy",
                state,
                observer_state="guard",
                proxy_id="hero:clairvoyance",
            )
        )

    assert observe()["Visible"] is False
    state[f"{SRD}.entities"]["guard"]["see_invisible"] = True
    assert observe()["Luminous fist-size orb"] is True
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "shadowfell"
    assert observe()["Visible"] is False
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "material"
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["blinded"]
    assert observe()["Visible"] is False


def test_public_npc_information_cast_uses_its_saved_daily_use_then_communicates(
    tmp_path, monkeypatch
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.test_srd_npc_spell_casting import _act, _prepare_mage

    def npc_case(spell):
        state, data, params = _case(spell)
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity("mage", [0, 5, 0], participant_ref="mage", creature_type="humanoid")
        )
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", npc_case)
    database = tmp_path / "npc-information.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            "speak_with_animals",
            target_overrides={"creature_type": "beast"},
        )
        _prepare_mage(owner, [{"spell": "speak_with_animals", "uses_per_day": 1}])
        params.pop("caster")
        params.pop("slot_resource")
        params["caster_state"] = "mage"
        receipt, command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][
                "speak_with_animals"
            ]
            == 0
        )
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        later, _ = _act(
            reopened,
            "communicate_srd_spell",
            listener_state="mage",
            speaker_state="guard",
            purpose="nearby_monsters",
        )
        summary = {row["label"]: row["value"] for row in later.result["result_card"]["summary"]}
        assert summary["Information available"] is True


def test_sensor_expiry_deactivates_its_actual_scene_entry_and_rejects_later_queries():
    state, _, _ = _cast("arcane_eye")
    _free(state)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    proxy = next(
        item
        for item in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if item["id"] == "hero:arcane_eye"
    )
    assert proxy["active"] is False
    _reject("query_srd_information_spell", state, caster_state="hero", spell="arcane_eye")


def test_nonconcentration_language_survives_caster_death_until_its_actual_duration():
    state, _, _ = _cast("tongues")
    _free(state)
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(is_alive=False, hp=0)
    entities["other"]["languages"] = ["common"]
    result = _summary(
        _run(
            "communicate_srd_spell",
            state,
            listener_state="other",
            speaker_state="guard",
        )
    )
    assert result["Understands"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    _reject("communicate_srd_spell", state, listener_state="other", speaker_state="guard")
