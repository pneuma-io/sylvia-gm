"""Retained family effects reuse genuine preparation, payment, and release dispatches."""

import hashlib
import os
import re
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full import srd_contingency_child_casting as child
from scripts.srd_full import srd_detection_mask_casting as mask_casting
from scripts.srd_full import srd_spell_detection_masks as masks
from scripts.srd_full.srd_control_behaviors import _walk
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

ROOT = Path(__file__).resolve().parents[2]
CURRENT = Path(
    os.environ.get(
        "FC14_CONTINGENCY_SOURCE_ROOT", str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-contingency-source-root")
    )
)
PARENT = Path(
    os.environ.get(
        "FC14_CONTINGENCY_CHILD_PARENT",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-contingency-child-parent/selected-runtime.yaml"),
    )
)


def _with_native_masks(text, definitions):
    """Add only this missing native family to the hash-verified historical proof ancestor."""
    names = ("arcanists_magic_aura", "nondetection", "pass_without_trace")
    assert all(definitions[name] == masks.PROFILES[name] for name in names)
    parsed = _load_runtime(text)
    missing = {
        name: definitions[name] for name in names if name not in parsed["definitions"]["spells"]
    }
    if missing:
        offset = text.index("  spells:\n", text.index("\ndefinitions:\n")) + len("  spells:\n")
        rows = yaml.safe_dump(missing, sort_keys=False)
        text = (
            text[:offset] + "".join("    " + line for line in rows.splitlines(True)) + text[offset:]
        )
        changed = {}
        for name in (
            "begin_srd_spell_cast_internal",
            "prepare_srd_contingent_child",
            "cast_spell_outside_combat",
            "cast_spell",
        ):
            tool = deepcopy(parsed["tools"][name])
            selector = next(p for p in tool["parameters"] if p["name"] == "spell")
            assert set(selector["enum_values"]) == set(parsed["definitions"]["spells"])
            selector["enum_values"] = [*parsed["definitions"]["spells"], *missing]
            changed[name] = tool
        text = child._emit_changed(text, changed)
    return mask_casting.extend_runtime(masks.extend_runtime(text))


@pytest.fixture(scope="module")
def actual():
    content = PARENT.read_bytes()
    assert hashlib.sha256(content).hexdigest() == (
        "c71f1b82ae647cbe6a4e413a6baf887c2f3c0586a60807a01d42945ac0404514"
    )
    text = PARENT.read_text()
    declarations = yaml.load(
        (CURRENT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text(),
        Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader),
    )
    definitions = {name: row["definition"] for name, row in declarations["spells"].items()}
    if "detection_mask_spell" in child.DISPATCHES:
        text = _with_native_masks(text, definitions)
    tools = {}
    for name in (
        "prepare_srd_contingent_child",
        "prepare_srd_contingency_cast",
        "install_srd_contingency_source",
        "cast_spell",
        "release_srd_contingent_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        *child.DISPATCHES.values(),
    ):
        match = re.search(
            rf"(?ms)^  {name}:[^\n]*\n.*?(?=^  [A-Za-z_][A-Za-z0-9_]*:|^definitions:|^procedures:)",
            text,
        )
        assert match, name
        tools[name] = _load_runtime("tools:\n" + match[0])["tools"][name]
    return tools, definitions


def test_actual_preparation_accepts_concentration_without_starting_or_spending_it(actual):
    tools, definitions = actual
    original = tools["prepare_srd_contingent_child"]
    prepared = deepcopy(original)
    selected = child._selected(definitions)
    assert "haste" in selected and "blur" in selected
    assert "tongues" in selected
    assert "speak_with_plants" not in selected and "divine_favor" not in selected
    child._prepare(prepared, selected)
    assert not any("decrement" in node for node in _walk(prepared["effects"]))
    assert not any(
        node.get("set", {}).get("field", "").startswith("caster_state.concentration")
        for node in _walk(prepared["effects"])
    )
    materials = [
        node for node in prepared["effects"] if node.get("call") == "resolve_srd_spell_materials"
    ]
    assert len(materials) == 1 and materials[0]["params"]["validate_only"] is True
    saved = next(
        node["set"]["value"]
        for node in prepared["effects"]
        if node.get("set", {}).get("field") == child.PREPARED
    )
    assert saved["casting_profile"] == "_casting_profile"
    assert saved["material_waived"] == "_material_waived"
    assert saved["caster_participant"] == "_caster_id"
    assert saved["concentration"] == "_contingent_concentration"
    once = deepcopy(prepared)
    child._prepare(prepared, selected)
    assert prepared == once and original == tools["prepare_srd_contingent_child"]


def test_setup_child_material_payment_uses_actual_owner_before_actual_slot_spend(actual):
    tools, definitions = actual
    prepared = deepcopy(tools["prepare_srd_contingent_child"])
    selected = child._selected(definitions)
    child._prepare(prepared, selected)
    material = next(
        node for node in prepared["effects"] if node.get("call") == "resolve_srd_spell_materials"
    )
    original = tools["install_srd_contingency_source"]
    install = deepcopy(original)
    child._install(install, selected, material)
    calls = [
        (index, node)
        for index, node in enumerate(install["effects"])
        if node.get("call") == "resolve_srd_spell_materials"
    ]
    assert len(calls) == 1
    index, payment = calls[0]
    assert payment["params"]["validate_only"] is False
    assert payment["params"]["spell"] == "{_contingent_setup.spell}"
    assert payment["params"]["components_waived"] == "{_contingent_setup.material_waived}"
    assert payment["params"]["target_count"] == 1
    assert install["effects"][index + 1]["decrement"] == {
        "field": "caster_state.resources.{_cast_resource}",
        "amount": 1,
    }
    once = deepcopy(install)
    child._install(install, selected, material)
    assert install == once and original == tools["install_srd_contingency_source"]


def test_family_call_retains_actual_exports_and_rejects_unknown_caller_verdict(actual):
    tools, _ = actual
    for name in child.DISPATCHES.values():
        original = next(n for n in _walk(tools["cast_spell"]["effects"]) if n.get("call") == name)
        call = child._dispatch(original)
        assert call["call"] == name
        assert call["export_context"] == original["export_context"]
        assert call["params"]["spell"] == "{_contingency_child.spell}"
        if "spell_cast_id" in original["params"]:
            assert call["params"]["spell_cast_id"] == "{_spell_source_cast_id}"
            assert call["params"]["spell_cast_owner"] == "{_target_state_id}"
        if "targets" in call["params"]:
            assert call["params"]["targets"] == "{_contingent_self_targets}"
        changed = deepcopy(original)
        changed["params"]["new_outcome"] = "{caller_outcome}"
        with pytest.raises(ValueError, match="no genuine retained binding"):
            child._dispatch(changed)


def test_actual_release_allocates_own_source_and_only_self_effect_after_parent_consumption(actual):
    tools, definitions = actual
    runtime = {"definitions": {"spells": definitions}, "tools": deepcopy(tools), "procedures": {}}
    text = yaml.safe_dump(runtime, sort_keys=False)
    emitted = child.bind_release(text)
    final = _load_runtime(emitted)["tools"]
    assert child.bind_release(emitted) == emitted
    release = final[child.RELEASE]
    assert not any("decrement" in n for n in _walk(release["effects"]))
    assert not any(n.get("call") == "spend_spell_casting_budget" for n in _walk(release["effects"]))
    assert not any(
        n.get("call") == "resolve_srd_spell_materials" for n in _walk(release["effects"])
    )
    body = next(n["on_true"] for n in release["effects"] if "on_true" in n)
    assert body[1] == {
        "set": {
            "field": "lasting_state.contingency_sources.{source_id}.child_release_completed",
            "value": True,
        }
    }
    assert body[2] == {"set": {"field": "_contingent_self_targets", "value": ["_target_state_id"]}}
    dispatch = next(n for n in body if n.get("condition") == "_contingent_effect_eligible == true")
    assert dispatch["on_true"][2]["call"] == "begin_srd_spell_source_instance"
    assert dispatch["on_true"][3]["set"]["field"].endswith(".child_cast_id")
    assert not any("call" in n for n in dispatch["on_false"])
    assert (
        next(
            n["set"]["value"]
            for n in dispatch["on_false"]
            if n.get("set", {}).get("field", "").endswith(".child_effect_applied")
        )
        is False
    )
    assert all(node["call"] != "cast_spell" for node in _walk(release["effects"]) if "call" in node)
    for name in tools.keys() - {"release_srd_contingent_spell", "prepare_srd_contingent_child"}:
        assert final[name] == tools[name]


def test_source_profile_change_cannot_admit_an_ineligible_child(actual):
    _, definitions = actual
    for field, value in (
        ("base_level", 6),
        ("casting_time", "bonus_action"),
        ("family", "object_content_spell"),
    ):
        changed = deepcopy(definitions)
        changed["haste"][field] = value
        with pytest.raises(ValueError, match="admitted contingent self-effect"):
            child._selected(changed)


def test_actual_extended_owners_retain_nondefault_choice_and_expanded_selector(actual):
    tools, definitions = actual
    runtime = {"definitions": {"spells": definitions}, "tools": deepcopy(tools), "procedures": {}}
    text = child.extend_runtime(yaml.safe_dump(runtime, sort_keys=False))
    before = _load_runtime(text)["tools"]
    text = child.bind_retained_choices(text)
    final = _load_runtime(text)["tools"]
    assert child.bind_retained_choices(text) == text
    selector = next(p for p in final["cast_spell"]["parameters"] if p["name"] == "contingent_spell")
    for name in (
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "cast_spell_outside_combat",
    ):
        params = {p["name"]: p for p in final[name]["parameters"]}
        assert params["contingent_spell"]["enum_values"] == selector["enum_values"]
        assert params[child.CHOICE] == child._choice()
        for node in _walk(final[name]["effects"]):
            if (
                node.get("set", {}).get("field")
                == "caster_state.extended_casting.contingency_choices"
            ):
                assert node["set"]["value"][child.CHOICE] == child.CHOICE
            if node.get("call") in {
                "begin_srd_spell_cast_internal",
                "cast_spell",
                "prepare_srd_contingency_cast",
            }:
                assert node["params"][child.CHOICE] == "{" + child.CHOICE + "}"
    for name in before.keys() - {
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
    }:
        assert final[name] == before[name]
