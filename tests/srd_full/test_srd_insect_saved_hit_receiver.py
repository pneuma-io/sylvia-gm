"""Saved native Web hits preserve wildcard feedback and their actual Speed consequence."""

from copy import deepcopy
from pathlib import Path

from scripts.srd_full.srd_attack_hit_riders import EVENT, _lists, _packet_clones
from scripts.srd_full.srd_giant_insect_actions import _web_receiver
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_final_saved_web_receiver_preserves_all_native_feedback_and_speed_suffix():
    tools = _load_runtime(
        (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    )["tools"]
    original = _web_receiver(tools)
    tools["apply_srd_insect_web_bolt_damage"] = original
    before = deepcopy(original)
    changed, aliases = _packet_clones(tools)
    saved = changed[aliases["apply_srd_insect_web_bolt_damage"]]
    packets = [
        effect
        for rows in _lists(saved["effects"])
        for effect in rows
        if effect.get("call") == aliases["apply_srd_qualifying_damage"]
    ]
    assert len(packets) == 1
    assert packets[0]["export_context"] == "*"
    assert packets[0]["params"][EVENT] == "{" + EVENT + "}"
    assert saved["effects"][-1] == original["effects"][-1]
    assert saved["effects"][-1]["call"] == "install_srd_native_insect_turn_effect"
    assert saved["effects"][-1]["params"]["kind"] == "speed_zero"
    assert original == before
