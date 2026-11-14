"""Self spells need no fictional target and still consume paid casts and later language."""

from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_information_casting import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_wave3_information as information
from tests.srd_full.test_srd_action_budget import SRD, _run


@lru_cache
def _runtime():
    source = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = bind_runtime_consumers(source.read_text(encoding="utf-8"))
    return _load_runtime(text), text.encode()


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


def test_paid_pc_self_information_without_target_then_actual_language():
    state, data, parameters = information._case("speak_with_animals")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "beast"
    parameters.pop("target")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    record = next(
        row
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row.get("spell") == "speak_with_animals"
    )
    assert record["target"] == record["caster"] == "hero"
    later = _run(
        "communicate_srd_spell",
        state,
        listener_state="hero",
        speaker_state="guard",
        purpose="nearby_monsters",
    )
    assert information._summary(later)["Information available"] is True


def test_self_target_binder_preserves_semantic_repeat():
    runtime, content = _runtime()
    assert _load_runtime(bind_runtime_consumers(content.decode())) == runtime


def test_strict_paid_npc_self_cast_no_target_reopen_retry_and_later_language(tmp_path, monkeypatch):
    from sylvia_gm.application import bundled_packages
    from tests import test_srd_npc_spell_casting as npc
    from tests import test_srd_public_spell_casting as public
    from tests import test_srd_spell_appearance as appearance
    from tests.srd_full.test_srd_dream_spell import _linked_drafts

    monkeypatch.setattr(bundled_packages, "_srd_drafts", _linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", _runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", set())
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    actual = npc._act

    def without_target(owner, tool_name, **parameters):
        if tool_name == "cast_spell_outside_combat":
            parameters.pop("target", None)
        return actual(owner, tool_name, **parameters)

    monkeypatch.setattr(npc, "_act", without_target)
    information.test_public_npc_information_cast_uses_its_saved_daily_use_then_communicates(
        tmp_path, monkeypatch
    )
