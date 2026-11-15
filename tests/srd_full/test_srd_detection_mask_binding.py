"""Appended masks use the actual completed consumer's resolved lasting-state namespace."""

import hashlib
import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_spell_detection_masks import PROFILES, _schema, _tools
from scripts.srd_full.srd_timed_actor_defenses import _call, _parameter, _set, _tool, _when
from scripts.srd_full.srd_timed_senses import _loop, _optional

from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_detection_masks as masks
from tests.srd_full.test_srd_action_budget import SRD, _run

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-detection-mask-binding/input-1"))
RUNTIME_SHA = "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
CONSUMERS = (
    "check_srd_illumination_subject",
    "resolve_srd_information_bearer_magic",
    "qualify_srd_information_candidate",
    "perceive_srd_information_proxy",
    "perceive_srd_mislead_proxy",
    "perceive_srd_project_image_proxy",
    "resolve_srd_control_behavior_conditions",
    "d20_test",
)


@lru_cache
def _runtime():
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == RUNTIME_SHA
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    runtime["definition_row_schemas"]["definitions.spells"]["variants"]["detection_mask_spell"] = (
        _schema()
    )
    runtime["tools"].update(_tools(runtime))
    runtime["tools"]["probe_actual_mask_bearer"] = _tool(
        "Read the actual admitted subject's apparent magical aura.",
        [_parameter("subject"), _optional("lasting_state", default="state", resolve="entity")],
        [
            _call("resolve_srd_detection_mask_subject", {"subject": "{subject}"}, ["_mask_scene"]),
            _call(
                "resolve_srd_information_bearer_magic",
                {"candidate": "{_mask_scene}", "lasting_state": "{_lasting_state_id}"},
                ["_bearer_magic", "_bearer_schools"],
            ),
        ],
    )
    runtime["tools"]["probe_saved_mask_sensor"] = _tool(
        "Perceive through an actual saved paid sensor.",
        [_parameter("caster_state", resolve="entity")],
        [
            _set("_mask_sensor_records", "state.turn_effects"),
            _loop(
                "_mask_sensor_records", "_mask_sensor_record",
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
    masks._bind_paid_cast(runtime)
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@pytest.fixture(autouse=True)
def _actual_wave14(monkeypatch):
    masks._mechanic.cache_clear()
    monkeypatch.setattr(masks, "_runtime", _runtime)
    monkeypatch.setattr(masks, "PARENT", PARENT)
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", masks._mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )
    yield
    masks._mechanic.cache_clear()


def _mask_calls(value):
    if isinstance(value, dict):
        if value.get("call") in {
            "check_srd_detection_mask_subject",
            "check_srd_pass_without_trace",
            "resolve_srd_sequester_condition_projection",
        }:
            yield value
        for child in value.values():
            yield from _mask_calls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _mask_calls(child)


def test_all_completed_delivery_hooks_bind_mask_state_and_repeat_without_duplication():
    runtime = deepcopy(_runtime()[0])
    for name in CONSUMERS:
        tool = runtime["tools"][name]
        assert not re.search(r"\bstate\.has_detection_masks\b", str(tool["effects"]))
        calls = list(_mask_calls(tool["effects"]))
        assert calls, name
        assert all(c["params"]["lasting_state"] == "{_lasting_state_id}" for c in calls)
    repeated = _tools(runtime)
    runtime["tools"].update(repeated)
    assert _tools(runtime) == repeated


def test_existing_legacy_hooks_are_repaired_without_adding_a_second_delivery_hook():
    runtime = deepcopy(_runtime()[0])

    def unbind(value):
        if isinstance(value, str):
            return value.replace("lasting_state.has_detection_masks", "state.has_detection_masks")
        if isinstance(value, list):
            return [unbind(child) for child in value]
        if isinstance(value, dict):
            result = {key: unbind(child) for key, child in value.items()}
            if result.get("call") in {
                "check_srd_detection_mask_subject",
                "check_srd_pass_without_trace",
                "resolve_srd_sequester_condition_projection",
            }:
                result["params"].pop("lasting_state", None)
            return result
        return value

    counts = {}
    for name in CONSUMERS:
        counts[name] = len(list(_mask_calls(runtime["tools"][name]["effects"])))
        runtime["tools"][name]["effects"] = unbind(runtime["tools"][name]["effects"])
    runtime["tools"].update(_tools(runtime))
    for name in CONSUMERS:
        assert runtime["tools"][name] == _runtime()[0]["tools"][name]
        assert len(list(_mask_calls(runtime["tools"][name]["effects"]))) == counts[name]


def test_paid_false_aura_is_delivered_by_actual_bearer_magic_after_reopen():
    state, _, _ = masks._cast("arcanists_magic_aura", "orb", mask_object_aura="nonmagical")
    state = json.loads(json.dumps(state))
    result = _run("probe_actual_mask_bearer", state, subject="orb").result["context"]
    assert result["_bearer_magic"] is False and result["_bearer_schools"] == []
    _run("retire_srd_detection_mask", state, instance=masks._record(state)["instance"])
    assert _run("probe_actual_mask_bearer", state, subject="orb").result["context"][
        "_bearer_magic"
    ] is True


def test_apparent_magic_uses_explicit_resolved_state_for_guard_and_saved_source_clock():
    state, _, _ = masks._cast("arcanists_magic_aura", "orb", mask_object_aura="nonmagical")
    source_state = deepcopy(state[f"{SRD}.state"])
    state[f"{SRD}.entities"]["mask_state"] = source_state
    state[f"{SRD}.state"]["has_detection_masks"] = False
    result = _run(
        "probe_actual_mask_bearer", state, subject="orb", lasting_state="mask_state"
    ).result["context"]
    assert result["_bearer_magic"] is False
    source_state["lasting_tick"] = masks._record(state)["expires_tick"]
    assert _run(
        "probe_actual_mask_bearer", state, subject="orb", lasting_state="mask_state"
    ).result["context"]["_bearer_magic"] is True


@pytest.mark.parametrize("modality", ["sight", "hearing"])
def test_paid_nondetection_blocks_actual_saved_sensor_subject_delivery(modality):
    masks.test_nondetection_blocks_saved_sensor_delivery_and_returns_after_removal(modality)


def test_paid_nondetection_changes_actual_information_candidate_delivery():
    state, data, _ = masks._cast("nondetection", "orb")
    masks._later_information_cast(state, data, "locate_object")
    result = _run(
        "query_srd_information_spell", state, caster_state="hero", spell="locate_object"
    ).result["context"]
    assert result["_info_found"] is False
    _run("retire_srd_detection_mask", state, instance=masks._record(state)["instance"])
    result = _run(
        "query_srd_information_spell", state, caster_state="hero", spell="locate_object"
    ).result["context"]
    assert result["_info_found"] is True


def test_paid_trace_source_adds_actual_stealth_modifier_and_stops_with_concentration():
    masks.test_pass_without_trace_actual_source_volume_stealth_and_tracks(
        20, "material", False, 10
    )


def test_paid_sequester_projects_actual_invisible_and_unconscious_sources():
    state, _, _ = masks._cast("sequester")
    result = _run("check_srd_illumination_subject", state, subject="guard").result["context"]
    assert result["_illumination_invisible"] is True
    result = _run(
        "resolve_srd_control_behavior_conditions", state, target_state="guard"
    ).result["context"]
    assert result["_control_unconscious"] is True
    _run("retire_srd_detection_mask", state, instance=masks._record(state)["instance"])
    assert state[f"{SRD}.entities"]["guard"]["conditions"] == []


@pytest.mark.parametrize("active", [False, True])
def test_inactive_ordinary_save_cost_card_and_math_preserve_completed_owner(active, monkeypatch):
    masks.test_ordinary_save_cost_card_math_and_state_are_unchanged_with_mask_consumer(
        active, monkeypatch
    )


def test_actual_strict_archive_paid_trace_roll_reopen_and_retry(tmp_path, monkeypatch):
    masks.test_actual_public_paid_trace_then_saved_stealth_card_reopen_and_retry(
        tmp_path, monkeypatch
    )
