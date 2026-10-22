"""Source masks derive later detection and Stealth from actual saved casts and scene facts."""

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_detection_masks import CHOICES, PROFILES, _schema, _tools
from scripts.srd_full.srd_timed_actor_defenses import _call, _parameter, _set, _tool, _when
from scripts.srd_full.srd_timed_senses import _loop, _push

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case
from tests.srd_full.test_srd_spell_wave3_information import _wall

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-detection-masks/input-1"))


def _bind_paid_cast(runtime):
    cast = runtime["tools"]["cast_spell"]
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CHOICES if p["name"] not in names]
    params = {p["name"]: "{" + p["name"] + "}" for p in CHOICES}
    params.update(
        caster_state="{_caster_state_id}",
        spell="{spell}",
        slot_level="{_slot_level}",
        save_dc="{_casting_profile.save_dc}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
    )
    call = _call("apply_srd_detection_mask", params, ["_spell_summary_rows", "_spell_detail_rows"])
    branch = _when("_spell_definition.family == 'detection_mask_spell'", [call])
    call["export_context"] = {
        "_mask_summary": "_spell_summary_rows",
        "_mask_details": "_spell_detail_rows",
    }
    branch["on_true"] += [
        _loop("_mask_summary", "_mask_card_row", [_push("_spell_summary_rows", "_mask_card_row")]),
        _loop("_mask_details", "_mask_card_row", [_push("_spell_detail_rows", "_mask_card_row")]),
    ]

    def bind(effects):
        indices = [
            i
            for i, e in enumerate(effects)
            if "_spell_definition.family ==" in e.get("condition", "")
        ]
        if indices:
            effects.insert(indices[-1] + 1, branch)
            return True
        for effect in effects:
            for name in ("on_true", "on_false"):
                if name in effect and bind(effect[name]):
                    return True
        return False

    assert bind(cast["effects"])
    outside = runtime["tools"]["cast_spell_outside_combat"]
    names = {p["name"] for p in outside["parameters"]}
    outside["parameters"] += [deepcopy(p) for p in CHOICES if p["name"] not in names]
    assert bind(outside["effects"])
    for tool in runtime["tools"].values():
        for parameter in tool.get("parameters", []):
            if parameter["name"] == "spell" and parameter["type"] == "enum":
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | PROFILES.keys())


@lru_cache
def _runtime():
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert (
        hashlib.sha256(payload).hexdigest()
        == "a5a2189ef46658a75777f09840d4b441569a739b93fd4aa8f3e7f0f205e52f0f"
    )
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["detection_mask_spell"] = (
        _schema()
    )
    runtime["tools"].update(_tools(runtime))
    runtime["tools"]["probe_actual_mask_bearer"] = _tool(
        "Read the actual admitted subject's apparent magical aura.",
        [_parameter("subject")],
        [
            _call("resolve_srd_detection_mask_subject", {"subject": "{subject}"}, ["_mask_scene"]),
            _call(
                "resolve_srd_information_bearer_magic",
                {"candidate": "{_mask_scene}"},
                ["_bearer_magic", "_bearer_schools"],
            ),
        ],
    )
    runtime["tools"]["probe_saved_mask_sensor"] = _tool(
        "Perceive through the actual saved sensor, preserving its caster and source choices.",
        [_parameter("caster_state", resolve="entity")],
        [
            _set("_mask_sensor_records", "state.turn_effects"),
            _loop(
                "_mask_sensor_records",
                "_mask_sensor_record",
                [
                    _when(
                        "_mask_sensor_record.kind == 'information_spell' and "
                        "_mask_sensor_record.spell == 'clairvoyance'",
                        [
                            _call(
                                "perceive_srd_information_proxy",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "record": "{_mask_sensor_record}",
                                },
                                ["_info_results"],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    _bind_paid_cast(runtime)
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
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
def _owning_runtime(monkeypatch):
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )


def _case(spell, subject="guard", **choices):
    state, data, params = _information_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, active_actor=None)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for entry in scene["entities"]:
        entry["footprint"] = {"kind": "box", "half_extents": [0.1, 0.1]}
    scene["entities"][1]["position"] = [5, 0, 0]
    scene["entities"][2]["position"] = [5, 0, 0]
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "fiend"
    params.update(mask_subject=subject, mask_willing=True)
    params.update(choices)
    return state, data, params


def _cast(spell, subject="guard", **choices):
    state, data, params = _case(spell, subject, **choices)
    _run("cast_spell", state, participant_data=data, **params)
    return state, data, params


def _entry(state, identity):
    return next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == identity
    )


def _record(state):
    return next(
        e for e in state[f"{SRD}.state"]["turn_effects"] if e.get("kind") == "detection_mask"
    )


def _query(state, subject="guard"):
    return _run("check_srd_detection_mask_subject", state, subject=subject).result["context"]


@pytest.mark.parametrize(
    "aura,school,magic,schools",
    [
        ("magical", "illusion", True, ["illusion"]),
        ("nonmagical", "illusion", False, []),
        ("school", "necromancy", True, ["necromancy"]),
    ],
)
def test_magic_aura_actual_paid_object_changes_only_apparent_magic(aura, school, magic, schools):
    state, _, _ = _cast("arcanists_magic_aura", "orb", mask_object_aura=aura, mask_school=school)
    state = json.loads(json.dumps(state))
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    assert _entry(state, "orb")["metadata"]["magical_effect"] is True
    result = _run("probe_actual_mask_bearer", state, subject="orb").result["context"]
    assert result["_bearer_magic"] is magic and result["_bearer_schools"] == schools
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert _query(state, "orb")["_mask_object_aura"] is None


def test_magic_aura_creature_has_source_mask_but_preserves_actual_creature_type():
    state, _, _ = _cast("arcanists_magic_aura", mask_creature_type="humanoid")
    assert _query(state)["_mask_creature_type"] == "humanoid"
    assert _entry(state, "guard")["metadata"]["creature_type"] == "fiend"
    assert state[f"{SRD}.entities"]["guard"]["creature_type"] == "fiend"


def test_magic_aura_rejects_conflicting_scene_and_prepared_creature_types():
    state, data, params = _case("arcanists_magic_aura")
    _entry(state, "guard")["metadata"]["creature_type"] = "undead"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="prepared creature"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize("holder", ["carried_by", "worn_by"])
def test_magic_aura_rejects_actual_held_object_atomically(holder):
    state, data, params = _case("arcanists_magic_aura", "orb")
    _entry(state, "orb")["metadata"][holder] = "hero"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="worn or carried"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


def test_nondetection_actual_cost_source_expiry_and_saved_alias_query():
    state, _, _ = _cast("nondetection")
    state = json.loads(json.dumps(state))
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 2
    assert _query(state)["_mask_divination_blocked"] and _query(state)["_mask_sensor_blocked"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=4799)
    assert _query(state)["_mask_divination_blocked"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("refresh_srd_detection_masks", state)
    assert not _query(state)["_mask_divination_blocked"]
    assert state[f"{SRD}.state"]["has_detection_masks"] is False


@pytest.mark.parametrize(
    "boundary,plane,wall,expected",
    [
        (30.2, "material", False, 10),
        (30.201, "material", False, 0),
        (20, "ethereal", False, 0),
        (20, "material", True, 0),
    ],
)
def test_pass_without_trace_actual_source_volume_stealth_and_tracks(
    boundary, plane, wall, expected
):
    state, _, _ = _cast("pass_without_trace", "hero", mask_chosen_creatures=["guard"])
    _entry(state, "guard")["position"] = [boundary, 0, 0]
    _entry(state, "guard")["metadata"]["plane"] = plane
    if wall:
        state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1, material="wood")]
    state = json.loads(json.dumps(state))
    qualified = _run("check_srd_pass_without_trace", state, target_state="guard").result["context"]
    assert qualified["_mask_stealth_bonus"] == expected
    assert qualified["_mask_trackless"] is (expected == 10)
    result = _run(
        "d20_test",
        state,
        actor_state="guard",
        kind="ability_check",
        ability="dexterity",
        skill="stealth",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
    ).result["context"]
    assert result["_combined_modifier"] == expected
    state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] = None
    assert not _run("check_srd_pass_without_trace", state, target_state="guard").result["context"][
        "_mask_trackless"
    ]


def test_sequester_exact_conditions_detection_and_retirement_preserve_independent_sources():
    state, data, params = _case("sequester")
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["invisible", "unconscious"]
    _run("cast_spell", state, participant_data=data, **params)
    source = _record(state)
    state = json.loads(json.dumps(state))
    mask = _query(state)
    assert mask["_mask_sequestered"] and mask["_mask_detect_magic_blocked"]
    assert set(state[f"{SRD}.entities"]["guard"]["conditions"]) == {"invisible", "unconscious"}
    _run("retire_srd_detection_mask", state, instance=source["instance"])
    assert not _query(state)["_mask_sequestered"]
    assert set(state[f"{SRD}.entities"]["guard"]["conditions"]) == {"invisible", "unconscious"}


def test_detection_mask_source_emission_is_idempotent():
    runtime, _ = _runtime()
    again = _tools(runtime)
    for name in set(again) & set(runtime["tools"]):
        assert again[name] == runtime["tools"][name]


def test_chosen_trace_creatures_are_canonical_actors_and_reject_objects_atomically():
    state, data, params = _case("pass_without_trace", "hero")
    _entry(state, "guard")["id"] = "guard-body"
    params["mask_chosen_creatures"] = ["guard-body", "guard", "guard-body"]
    _run("cast_spell", state, participant_data=data, **params)
    assert _record(state)["chosen_creatures"] == ["guard"]
    assert (
        _run("check_srd_pass_without_trace", state, target_state="guard").result["context"][
            "_mask_stealth_bonus"
        ]
        == 10
    )
    state, data, params = _case("pass_without_trace", "hero", mask_chosen_creatures=["orb"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual creatures"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


def test_trace_retirement_clears_only_its_exact_concentration_source():
    state, _, _ = _cast("pass_without_trace", "hero")
    source = _record(state)
    _run("retire_srd_detection_mask", state, instance=source["instance"])
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    state, _, _ = _cast("pass_without_trace", "hero")
    source = _record(state)
    state[f"{SRD}.entities"]["hero"]["mask_concentration_instance"] += 1
    before = deepcopy(state[f"{SRD}.entities"]["hero"])
    _run("retire_srd_detection_mask", state, instance=source["instance"])
    assert state[f"{SRD}.entities"]["hero"] == before


def _install_retained(state, spell, subject="guard", slot=2, **choices):
    """Exercise history after the first paid producer, with actual source clock steps."""
    return _run(
        "apply_srd_detection_mask",
        state,
        caster_state="hero",
        spell=spell,
        slot_level=slot,
        save_dc=18,
        mask_subject=subject,
        mask_willing=True,
        **choices,
    )


def test_aura_same_day_does_not_advance_and_thirty_actual_days_make_source_permanent():
    state, _, _ = _cast("arcanists_magic_aura", "orb")
    _install_retained(state, "arcanists_magic_aura", "orb")
    assert state[f"{SRD}.state"]["magic_aura_cast_days"][0]["count"] == 1
    for _ in range(29):
        _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
        _install_retained(state, "arcanists_magic_aura", "orb")
    assert _record(state)["expires_tick"] is None
    assert state[f"{SRD}.state"]["magic_aura_cast_days"][0]["count"] == 30
    state = json.loads(json.dumps(state))
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert _query(state, "orb")["_mask_magic_aura_active"]


def test_aura_missing_day_resets_history_and_higher_slot_then_newest_wins():
    state, _, _ = _cast(
        "arcanists_magic_aura", "orb", mask_object_aura="school", mask_school="necromancy"
    )
    _install_retained(state, "arcanists_magic_aura", "orb", slot=4, mask_object_aura="nonmagical")
    _install_retained(state, "arcanists_magic_aura", "orb", slot=2, mask_object_aura="magical")
    assert _query(state, "orb")["_mask_object_aura"] == "nonmagical"
    _install_retained(
        state,
        "arcanists_magic_aura",
        "orb",
        slot=4,
        mask_object_aura="school",
        mask_school="evocation",
    )
    assert _query(state, "orb")["_mask_school"] == "evocation"
    _run("advance_srd_lasting_time", state, elapsed_rounds=28800)
    _install_retained(state, "arcanists_magic_aura", "orb")
    assert state[f"{SRD}.state"]["magic_aura_cast_days"][0]["count"] == 1


def test_target_school_and_creature_type_come_from_actual_saved_source():
    state, _, _ = _cast("nondetection")
    for spell, expected in [("detect_magic", False), ("fire_bolt", True)]:
        result = _run(
            "qualify_srd_detection_mask_target", state, subject="guard", spell=spell
        ).result["context"]
        assert result["_mask_target_permitted"] is expected
    state, _, _ = _cast("arcanists_magic_aura", mask_creature_type="undead")
    result = _run("resolve_srd_magical_creature_type", state, target_state="guard").result[
        "context"
    ]
    assert result["_magical_creature_type"] == "undead"
    assert state[f"{SRD}.entities"]["guard"]["creature_type"] == "fiend"


def test_sequester_real_timer_and_suspension_remove_only_authored_conditions():
    state, _, _ = _cast("sequester", mask_end_after_rounds=2)
    assert set(state[f"{SRD}.entities"]["guard"]["conditions"]) == {"invisible", "unconscious"}
    flags = _run("check_srd_sequester_suspension", state, target_state="guard").result["context"]
    assert flags["_sequester_suspended"] and flags["_sequester_no_air_required"]
    state = json.loads(json.dumps(state))
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("refresh_srd_detection_masks", state)
    assert _query(state)["_mask_sequestered"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("refresh_srd_detection_masks", state)
    assert not _query(state)["_mask_sequestered"]
    assert state[f"{SRD}.entities"]["guard"]["conditions"] == []


@pytest.mark.parametrize(
    "distance,plane,ends",
    [(5280, "material", True), (5280.01, "material", False), (5, "ethereal", False)],
)
def test_sequester_declared_condition_uses_actual_event_boundary(distance, plane, ends):
    state, _, _ = _cast("sequester", mask_end_description="the admitted bell rings")
    source = _record(state)
    _entry(state, "orb")["position"] = [5 + distance, 0, 0]
    _entry(state, "orb")["metadata"]["plane"] = plane
    state = json.loads(json.dumps(state))
    result = _run(
        "resolve_srd_sequester_condition",
        state,
        instance=source["instance"],
        event_subject="orb",
        fictional_match=True,
    ).result["context"]
    assert result["_sequester_event_ended"] is ends
    assert _query(state)["_mask_sequestered"] is (not ends)


@pytest.mark.parametrize("subject", ["guard", "orb"])
def test_sequester_exact_subject_retirement_survives_reopen(subject):
    state, _, _ = _cast("sequester", subject)
    state = json.loads(json.dumps(state))
    _run("end_srd_sequester_for_subject", state, subject="hero")
    assert _query(state, subject)["_mask_sequestered"]
    _run("end_srd_sequester_for_subject", state, subject=subject)
    assert not _query(state, subject)["_mask_sequestered"]


def test_sequester_object_visibility_uses_its_actual_source_and_current_holder():
    state, _, _ = _cast("sequester", "orb")
    state = json.loads(json.dumps(state))
    assert not _entry(state, "orb")["metadata"].get("invisible", False)
    _entry(state, "orb")["metadata"]["carried_by"] = "hero"
    _entry(state, "hero")["position"] = [25, 5, 0]
    result = _run("check_srd_illumination_object_visibility", state, object_id="orb").result[
        "context"
    ]
    assert result["_illumination_object_invisible"]
    assert result["_illumination_object_origin"] == [25, 5, 0]
    _run("retire_srd_detection_mask", state, instance=_record(state)["instance"])
    result = _run("check_srd_illumination_object_visibility", state, object_id="orb").result[
        "context"
    ]
    assert not result["_illumination_object_invisible"]


def test_sequester_respects_actual_unconscious_immunity_and_preserves_native_zero_hp():
    state, data, params = _case("sequester")
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["unconscious"]
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["guard"]["conditions"] == ["invisible"]
    state, data, params = _case("sequester")
    state[f"{SRD}.entities"]["guard"].update(hp=0, conditions=["unconscious"])
    _run("cast_spell", state, participant_data=data, **params)
    _run("retire_srd_detection_mask", state, instance=_record(state)["instance"])
    assert state[f"{SRD}.entities"]["guard"]["conditions"] == ["unconscious"]


@pytest.mark.parametrize("spell,minimum", [("nondetection", 2500), ("sequester", 500000)])
def test_real_material_threshold_rejects_without_cost_and_exact_value_is_consumed(spell, minimum):
    state, data, params = _case(spell)
    material = state[f"{SRD}.entities"]["hero"]["spell_materials"][0]
    assert material["value_cp"] == minimum
    material["value_cp"] = minimum - 1
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before
    material["value_cp"] = minimum
    _run("cast_spell", state, participant_data=data, **params)
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 2


def test_touch_cast_on_actual_floor_retains_source_but_real_wall_rejects():
    state, data, params = _case("nondetection")
    floor = _wall(1, material="stone")
    floor["volume"] = {"kind": "box", "min": [-100, -100, -1], "max": [100, 100, 0]}
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [floor]
    _run("cast_spell", state, participant_data=data, **params)
    assert _query(state)["_mask_divination_blocked"]
    state, data, params = _case("nondetection")
    wall = _wall(1, material="stone")
    wall["volume"] = {"kind": "box", "min": [2, -5, -5], "max": [3, 5, 5]}
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="total cover"):
        _run("cast_spell", state, participant_data=data, **params)
    assert state == before


def _later_information_cast(state, data, spell, **choices):
    """Prepare another printed Wizard spell and its actual material row before paid casting."""
    prepared, _, params = _information_case(spell)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append(spell)
    data["spellcasting_grants"][0]["spells"].append(spell)
    data["wizard_spellbook"].append(spell)
    materials = deepcopy(prepared[f"{SRD}.entities"]["hero"]["spell_materials"])
    for item in materials:
        item["id"] = "later_" + item["id"]
        item["inventory_item"] = "later_" + item["inventory_item"]
    hero["spell_materials"] += materials
    hero["inventory"] += [item["inventory_item"] for item in materials]
    params["material_item_ids"] = [item["id"] for item in materials]
    params.update(choices)
    if spell == "clairvoyance":
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


@pytest.mark.parametrize("modality", ["sight", "hearing"])
def test_nondetection_blocks_saved_sensor_delivery_and_returns_after_removal(modality):
    state, data, _ = _cast("nondetection")
    _later_information_cast(state, data, "clairvoyance", information_modality=modality)
    source = next(
        r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "clairvoyance"
    )
    _entry(state, "guard")["metadata"]["audible_to"] = [source["proxy_id"]]
    state = json.loads(json.dumps(state))
    result = _run("probe_saved_mask_sensor", state, caster_state="hero").result["context"]
    assert not any("guard:" in row for row in result["_info_results"])
    _run("retire_srd_detection_mask", state, instance=_record(state)["instance"])
    result = _run("probe_saved_mask_sensor", state, caster_state="hero").result["context"]
    assert any("guard:" in row for row in result["_info_results"])


def test_actual_magic_query_uses_object_false_aura():
    state, data, _ = _cast("arcanists_magic_aura", "orb", mask_object_aura="nonmagical")
    _later_information_cast(state, data, "detect_magic")
    result = _run(
        "query_srd_information_spell", state, caster_state="hero", spell="detect_magic", mode="aura"
    ).result["result_card"]
    assert "orb:" not in str(result)
    _run("retire_srd_detection_mask", state, instance=_record(state)["instance"])
    result = _run(
        "query_srd_information_spell", state, caster_state="hero", spell="detect_magic", mode="aura"
    ).result["result_card"]
    assert "orb:" in str(result)


def _test_archive():
    runtime, payload = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for row in record["tools"]:
        if row["name"] in runtime["tools"]:
            row["input_schema"] = _tool_schema(runtime["tools"][row["name"]], tool_name=row["name"])
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": payload}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "independent-detection-mask-owner",
            "kind": "ruleset_runtime",
            "family": "detection_mask_spell",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": ["cast_spell_outside_combat", "d20_test"],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["mask-paid-cast-stealth-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payloads, primary.format_version), reference))


def test_actual_public_paid_trace_then_saved_stealth_card_reopen_and_retry(tmp_path, monkeypatch):
    import sylvia_gm.application.bundled_packages as bundled

    monkeypatch.setattr(bundled, "_bundled_srd_root", lambda: PARENT / "rulesets/dnd-srd-5.2.1")
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(
        public_casting,
        "_spell_case",
        lambda spell: _case(spell, "hero", mask_chosen_creatures=["guard"]),
    )
    database = tmp_path / "masks.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "pass_without_trace")
        result, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert result.status is ActionStatus.COMMITTED, result.result
        saved_source = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved_source
        roll, command = _act(
            owner,
            "d20_test",
            actor_state="guard",
            kind="ability_check",
            ability="dexterity",
            skill="stealth",
            ability_modifier=0,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=10,
        )
        assert roll.status is ActionStatus.COMMITTED, roll.result
        assert roll.result["context"]["_combined_modifier"] == 10
        saved_roll = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved_roll

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A saved mask consumer retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll(99))
        assert owner.execute_action(command) == roll
        assert owner.snapshot("casting") == saved_roll


@pytest.mark.parametrize("active", [False, True])
def test_ordinary_save_cost_card_math_and_state_are_unchanged_with_mask_consumer(
    active, monkeypatch
):
    runtime, _ = _runtime()
    baseline = deepcopy(runtime)
    original = yaml.load(
        (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes(),
        Loader=yaml.CSafeLoader,
    )
    baseline["tools"]["d20_test"] = deepcopy(original["tools"]["d20_test"])
    state, _, _ = _cast("pass_without_trace", "hero", mask_chosen_creatures=["guard"])
    if not active:
        _run("retire_srd_detection_mask", state, instance=_record(state)["instance"])
    measured = []
    count = 0
    consume = ExecutionBudget.consume_operation

    def charge(budget):
        nonlocal count
        count += 1
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", charge)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    for rules in (baseline, runtime):
        mechanic = materialize_package_mechanic(
            package_id=SRD,
            package_version="0.3.1",
            package_sha256="a" * 64,
            runtime_bytes=yaml.safe_dump(rules, sort_keys=False).encode(),
            declaration=ToolDeclaration(
                name="d20_test",
                capability="dnd.srd.5.2.1.spells",
                input_schema=owned_payload(
                    _tool_schema(rules["tools"]["d20_test"], tool_name="d20_test")
                ),
                writers=("dm",),
                turn_statuses=("open",),
                state_predicates=(),
            ),
        )
        monkeypatch.setattr(test_srd_action_budget, "_mechanic", lambda _, m=mechanic: m)
        monkeypatch.setattr(dice, "_rng", Random(73))
        saved = deepcopy(state)
        count = 0
        result = _run(
            "d20_test",
            saved,
            actor_state="guard",
            kind="saving_throw",
            ability="dexterity",
            ability_modifier=0,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=10,
        )
        measured.append((result, saved, count))
    assert measured[0] == measured[1]
    print(f"Mask ordinary-save operations: {measured[0][2]} == {measured[1][2]}")
