"""Static saved-owner derivation retains the actual native Fist receiver and card fields."""

import hashlib
import re

import yaml
from scripts.srd_full.srd_reaction_spells import _attack_window, _complete_window, _typed_attack_feedback
from scripts.srd_full.srd_spell_summoning import _walk

from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations
from tests.srd_full.test_srd_native_wight_rise import DEPENDENCIES

FIST_FIELDS = {
    "_mummy_fist_necrotic_roll",
    "_mummy_fist_necrotic_amount",
    "_component_1_effective_damage",
    "_component_2_effective_damage",
    "_mummy_rot_source_id",
    "_mummy_fist_dust",
}


def _actual_attack_fragment():
    """Parse only two genuine owner declarations and their literal parameter pool."""
    raw = (DEPENDENCIES / "wight-public-parent-runtime.yaml").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "00a7de081d9880701c9bd84f339b10b53ff7eb99a945fc5e39f19b5e325099d5"
    )
    text = raw.decode().replace("\r\n", "\n")
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    tools = {}
    for name in ("resolve_adjudicated_creature_attack", "complete_srd_character_attack"):
        start = text.index("\n  " + name + ":\n") + 1
        end = re.search(r"^  [a-z][a-z0-9_]*:\s*$", text[start + 3 :], re.MULTILINE)
        assert end is not None
        stop = start + 3 + end.start()
        tools.update(yaml.load("tools:\n" + text[start:stop], Loader=loader)["tools"])
    start = text.index("\nparameter_declarations:\n") + 1
    pool = yaml.load(text[start:], Loader=loader)["parameter_declarations"]
    used = {
        parameter
        for tool in tools.values()
        for parameter in tool["parameters"]
        if isinstance(parameter, str)
    }
    return resolve_parameter_declarations(
        {"tools": tools, "parameter_declarations": {name: pool[name] for name in used}}
    )["tools"]


def test_saved_native_fist_capture_keeps_fresh_receiver_exports_and_card_values():
    tools = _actual_attack_fragment()
    owner = tools["resolve_adjudicated_creature_attack"]
    calls = [
        effect
        for effect in _walk(owner["effects"])
        if effect.get("call") == "apply_srd_native_mummy_fist_damage"
    ]
    assert calls and all(call["export_context"].keys() >= FIST_FIELDS for call in calls)
    assert set(_typed_attack_feedback(owner)) >= FIST_FIELDS
    saved = _attack_window(owner, "begin_srd_creature_attack", "actor", "creature_attack")
    completion = saved["complete_srd_creature_attack"]
    assert {
        effect["set"]["field"]
        for effect in completion["effects"]
        if "set" in effect and effect["set"].get("value") is None
    } >= FIST_FIELDS
    tools.update(saved)
    public = _complete_window(tools)
    call = next(
        effect
        for effect in _walk(public["effects"])
        if effect.get("call") == "complete_srd_creature_attack"
    )
    assert all(call["export_context"][field] == field for field in FIST_FIELDS)
    mummy = next(
        effect
        for effect in _walk(public["effects"])
        if effect.get("condition") == "_mummy_fist_necrotic_roll != null"
    )
    texts = [
        effect["params"]["text"]
        for effect in _walk(mummy["on_true"])
        if effect.get("call") == "record_srd_result_text"
    ]
    values = [
        effect["list_push"]["value"]["value"]
        for effect in _walk(mummy["on_true"])
        if effect.get("list_push", {}).get("list") == "_pending_card_details"
    ]
    presentation = " ".join(str(value) for value in [*texts, *values])
    assert all(
        field in presentation
        for field in FIST_FIELDS - {"_mummy_rot_source_id", "_mummy_fist_dust"}
    )
    assert "_mummy_rot_source_id" not in presentation
    branches = list(_walk(public["effects"]))
    rot = next(e for e in branches if e.get("condition") == "_mummy_rot_source_id != null")
    dust = next(e for e in branches if e.get("condition") == "_mummy_fist_dust == true")
    assert "Applied Mummy Rot" in str(rot["on_true"])
    assert "Reduced to dust" in str(dust["on_true"])
