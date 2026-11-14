"""A paid narrative occurrence releases the real saved child once, without a trigger verdict."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_contingency_water_trigger as water
from scripts import srd_contingency_world_fact as facts
from scripts.srd_full.srd_control_behaviors import _walk
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests import test_srd_contingency_combined_public as combined
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act

PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_WORLD_FACT_RUNTIME",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-contingency-world-fact-runtime/selected-runtime.yaml"),
    )
)
PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-contingency-world-fact/selected-runtime.yaml")
)


def test_source_observer_and_choice_adapters_preserve_native_queue_and_siblings():
    assert hashlib.sha256(PARENT.read_bytes()).hexdigest() == (
        "0a951053d87b0f2110ae3497e707a377ffffa60051fc598ec69bb5698459582d"
    )
    text = PARENT.read_text()
    before = _load_runtime(text)["tools"]
    changed = facts.bind_retained_choices(facts.extend_runtime(text))
    after = _load_runtime(changed)["tools"]
    own = {
        "prepare_srd_contingency_cast",
        "install_srd_contingency_source",
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        facts.OBSERVER,
        facts.RECORDER,
    }
    assert {n: t for n, t in before.items() if n not in own} == {
        n: t for n, t in after.items() if n not in own
    }
    assert facts.bind_retained_choices(facts.extend_runtime(changed)) == changed
    assert facts.normalize_trigger_choice(facts.normalize_trigger_choice(changed)) == (
        facts.normalize_trigger_choice(changed)
    )
    repeated_water = water.extend_runtime(facts.normalize_trigger_choice(changed))
    repeated_water = water.bind_extended_choices(repeated_water)
    repeated = facts.bind_retained_choices(facts.extend_runtime(repeated_water))
    assert _load_runtime(repeated)["tools"] == after
    recorder = after[facts.RECORDER]
    assert [p["name"] for p in recorder["parameters"]] == [
        "caster_state",
        "fact_tag",
        "lasting_state",
    ]
    assert recorder["parameters"][-1]["model_hidden"] is True
    assert any(n.get("call") == facts.DRAIN for n in _walk(recorder["effects"]))
    observer = after[facts.OBSERVER]
    assert not any("hp_before" in str(n) for n in _walk(observer["effects"]))
    assert any(n.get("call") == "check_srd_contingency_source" for n in _walk(observer["effects"]))
    receipts = [
        n["set"]
        for n in _walk(observer["effects"])
        if n.get("set", {}).get("field", "").endswith(".trigger_occurrence")
    ]
    assert len(receipts) == 1 and receipts[0]["value"]["source_id"] == "_contingency_source_id"
    assert not any(
        n.get("call") == facts.OBSERVER
        for n in _walk(after["install_srd_contingency_source"]["effects"])
    )


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == (
        "04acffad4e7180abf6c0e785ac0ebb40b2f62dea476c2c792f53c92ce0151fa6"
    )
    prior = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | {facts.RECORDER}
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF, combined.PUBLIC = prior
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


def test_paid_narrative_occurrence_not_hp_releases_once_and_reopens_retry(
    tmp_path,
    monkeypatch,
    actual_archive,
):
    state, data, parameters, materials = combined._case(monkeypatch)
    parameters |= {facts.CHOICE: facts.NARRATIVE, facts.TAG: "royal_bell_rings"}
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    database = tmp_path / "narrative.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        breathing, _ = _act(
            owner, "set_srd_respiration", target_state="hero", target_character="hero", medium="air"
        )
        assert breathing.status is ActionStatus.COMMITTED, breathing.result
        paid, paid_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        hero = armed.state[f"{combined.SRD}.entities"]["hero"]
        source_id = hero["contingency_source_id"]
        source = armed.state[f"{combined.SRD}.state"]["contingency_sources"][source_id]
        assert source["status"] == "armed" and source["installed"] is True
        assert source["trigger"] == facts.NARRATIVE and source["fact_tag"] == "royal_bell_rings"
        assert "trigger_occurrence" not in source
        resources = deepcopy(hero["resources"])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == armed
        wrong, wrong_command = _act(
            owner, facts.RECORDER, caster_state="hero", fact_tag="gate_opens"
        )
        assert wrong.status is ActionStatus.COMMITTED, wrong.result
        wrong_state = owner.snapshot("casting")
        assert (
            wrong_state.state[f"{combined.SRD}.state"]["contingency_sources"][source_id] == source
        )
        missile = {k: v for k, v in parameters.items() if not k.startswith("contingent_")} | {
            "spell": "magic_missile",
            "slot_resource": "spell_slot_2",
            "target": "hero",
            "material_item_ids": [],
        }
        hit, hit_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert hit.status is ActionStatus.COMMITTED, hit.result
        struck = owner.snapshot("casting")
        assert struck.state[f"{combined.SRD}.state"]["contingency_sources"][source_id] == source
        matched, matched_command = _act(
            owner,
            facts.RECORDER,
            caster_state="hero",
            fact_tag="royal_bell_rings",
        )
        assert matched.status is ActionStatus.COMMITTED, matched.result
        released = owner.snapshot("casting")
        lasting = released.state[f"{combined.SRD}.state"]
        hero = released.state[f"{combined.SRD}.entities"]["hero"]
        actual = lasting["contingency_sources"][source_id]
        assert actual["status"] == "released" and actual["installed"] is False
        assert actual["trigger_occurrence"] == {
            "tag": "royal_bell_rings",
            "caster": "hero",
            "source_id": source_id,
            "tick": lasting["lasting_tick"],
        }
        assert "trigger_hp_before" not in actual and "trigger_medium" not in actual
        assert lasting["contingency_release_queue"] == []
        assert hero["respiration_water_until"] > lasting["lasting_tick"]
        expected = dict(resources)
        expected["spell_slot_2"] -= 1
        assert dict(hero["resources"]) == expected
        child_sources = deepcopy(
            [e for e in lasting["turn_effects"] if e.get("spell") == "water_breathing"]
        )
        repeated, repeated_command = _act(
            owner,
            facts.RECORDER,
            caster_state="hero",
            fact_tag="royal_bell_rings",
        )
        assert repeated.status is ActionStatus.COMMITTED, repeated.result
        final = owner.snapshot("casting")
        assert final.state[f"{combined.SRD}.state"]["contingency_sources"][source_id] == actual
        assert [
            e
            for e in final.state[f"{combined.SRD}.state"]["turn_effects"]
            if e.get("spell") == "water_breathing"
        ] == child_sources
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("Saved retry must not reroll."))
        for command, receipt in (
            (paid_command, paid),
            (wrong_command, wrong),
            (hit_command, hit),
            (matched_command, matched),
            (repeated_command, repeated),
        ):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("casting") == final
