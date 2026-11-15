"""Late sensory extraction preserves every other existing owner and genuine cast identity."""

from pathlib import Path

import pytest
from scripts.srd_full.srd_conjured_presences import _lists, _sight, _subject_sight
from scripts.srd_full.srd_conjured_sight import emit_late_sight
from scripts.srd_full.srd_spell_travel import _normalize
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from tests import test_srd_action_budget as budget
from tests import test_srd_conjured_presences as presence
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _TravelDice, dice

actual_fall_scope = presence.actual_fall_scope
conjured_scope = presence.conjured_scope


@pytest.fixture
def allocated_presence_scope(conjured_scope, monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/conjured-root-join-v1/excluded/runtime.yaml")
    )


@pytest.mark.parametrize("spell", ["conjure_celestial", "conjure_fey"])
def test_actual_paid_cast_allocates_one_identity_and_installer_retains_it(
    allocated_presence_scope, actual_fall_scope, monkeypatch, spell
):
    state, data, params = presence._case(spell)
    if spell == "conjure_fey":
        params["fey_attack_target"] = ""
        rolls = []
    else:
        rolls = [(12, 2)] * 6 + [(12, 3)] * 4 + [(20, 10)]
    game = _TravelDice(rolls)
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    budget._run("cast_spell", state, participant_data=data, **params)
    lasting = state[f"{SRD}.state"]
    caster = state[f"{SRD}.entities"]["hero"]
    source = presence._source(state)
    assert lasting["spell_source_serial"] == 1
    assert source["cast_id"] == caster["last_spell_source_cast_id"] == "srd_cast:1"
    assert caster["last_spell_source_spell"] == spell
    assert source["source_caster_id"] == "hero" and source["ongoing_duration"] is True
    assert caster["concentration_cast_id"] == source["cast_id"]
    assert (
        caster["resources"]["spell_slot_7" if spell == "conjure_celestial" else "spell_slot_6"] == 2
    )
    assert (
        source["slot_level"] == source["spell_level"] == (7 if spell == "conjure_celestial" else 6)
    )
    if spell == "conjure_fey":
        spirit = next(
            e for e in lasting["spatial_scene"]["entities"] if e["id"] == source["subject_id"]
        )
        assert spirit["metadata"]["conjured_presence"]["cast_id"] == source["cast_id"]
    assert not game.rolls


def test_late_conjured_sight_changes_only_owned_clones_and_repeats_semantically():
    text = Path(".scratch/conjured-presences-frozen-v1/excluded/runtime.yaml").read_text()
    before = _load_runtime(text)
    after = _load_runtime(emit_late_sight(text))
    expected = {
        "resolve_srd_conjured_point_sight": _sight(before["tools"]),
        "resolve_srd_conjured_subject_sight": _subject_sight(before["tools"]),
    }
    _normalize(expected)
    for name, tool in before["tools"].items():
        assert after["tools"][name] == expected.get(name, tool)
    assert {key: value for key, value in before.items() if key != "tools"} == {
        key: value for key, value in after.items() if key != "tools"
    }
    assert _load_runtime(emit_late_sight(emit_late_sight(text))) == after
    for tool in expected.values():
        calls = {e.get("call") for sequence in _lists(tool["effects"]) for e in sequence}
        assert "resolve_srd_symbol_glyph_sight" in calls
        assert "resolve_srd_sight" not in calls
        assert "resolve_srd_travel_perception" not in calls
