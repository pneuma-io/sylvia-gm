"""Native healing, information and lasting buffs release once from their genuinely paid source."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full import srd_contingency_child_casting as child
from scripts.srd_full.srd_control_behaviors import _walk
from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._types import ExecutionContext
from tests.srd_full import test_srd_contingency_child_casting as native
from tests.srd_full import test_srd_contingency_child_public as prior
from tests.srd_full import test_srd_contingency_combined_public as combined
from tests.srd_full import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act

PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_CHILD_FAMILIES_RUNTIME",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-contingency-child-families-runtime/selected-runtime.yaml"),
    )
)


def test_retained_native_effects_and_nonpayment_guards_are_preserved():
    tools, _ = native.actual.__wrapped__()
    for family, name in child.DISPATCHES.items():
        if family in {"timed_actor_defense", "timed_actor_modifier", "timed_senses"}:
            continue
        original = tools[name]
        retained = child._retained_family(original)
        expected = deepcopy(original["effects"])
        if family == "detection_mask_spell":
            initializer = next(
                n["set"] for n in expected if n.get("set", {}).get("field") == "_mask_subject"
            )
            assert initializer["value"] == "mask_subject"
            initializer["value"] = "_caster_state_id"
        assert retained["effects"] == expected
        assert retained["metadata"] == original["metadata"]
        assert not any("decrement" in node for node in _walk(retained["effects"]))
        assert not any(
            "prepared_spells" in gate["condition"] for gate in retained["pre_conditions"]
        )
        for gate in original.get("pre_conditions", []):
            if "prepared_spells" not in gate["condition"]:
                assert gate in retained["pre_conditions"]
        assert retained["pre_conditions"][0]["condition"].endswith(repr(child.SOURCE_SHA256))
        assert ".child.spell == spell" in retained["pre_conditions"][1]["condition"]
        assert child._retained_family(retained) == retained
        assert tools[name] == original
    changed = deepcopy(tools["cast_spell_healing"])
    changed["pre_conditions"][0]["condition"] += " and caller_allowed == true"
    with pytest.raises(ValueError, match="known native preparation gate"):
        child._retained_family(changed)


def test_self_choices_use_only_printed_abilities_and_damage_types():
    tools, definitions = native.actual.__wrapped__()
    assert len(child._selected(definitions)) == 45
    assert "constitution" not in child.ABILITIES
    assert "constitution" in child._choice()["enum_values"]  # Restoration's native choice.
    preparation = deepcopy(tools["prepare_srd_contingent_child"])
    child._prepare(preparation, child._selected(definitions))
    conditions = [
        node["require"]["condition"] for node in preparation["effects"] if "require" in node
    ]
    assert any("spell != 'enhance_ability'" in condition for condition in conditions)
    assert any("spell != 'protection_from_energy'" in condition for condition in conditions)
    assert not any(
        parameter["name"] in {"eligible", "outcome", "triggered"}
        for parameter in preparation["parameters"]
    )


def test_self_scene_eligibility_uses_actual_unique_scene_and_local_count():
    row = {
        "id": "body",
        "active": True,
        "metadata": {"participant_ref": "hero", "plane": "material"},
    }
    for entries, allowed in (([row], True), ([], False), ([row, deepcopy(row)], False)):
        context = ExecutionContext(
            params={
                "lasting_state": {
                    "has_magic_jar_sources": False,
                    "spatial_scene": {
                        "map": {"grid": {"unit": "ft"}},
                        "entities": entries,
                    },
                }
            },
            vars={"_target_state_id": "hero", "_contingent_effect_eligible": True},
        )
        execute_effects(child._self_scene_eligibility(), context)
        assert context.vars["_contingent_scene_matches"] == len(entries)
        assert context.vars["_contingent_effect_eligible"] is allowed


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == (
        "0a951053d87b0f2110ae3497e707a377ffffa60051fc598ec69bb5698459582d"
    )
    original = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | {"query_srd_information_spell"}
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF, combined.PUBLIC = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


@pytest.mark.parametrize("spell", ["cure_wounds", "longstrider", "detect_evil_and_good"])
def test_paid_native_family_release_reopen_and_retry_without_another_child_cost(
    tmp_path, monkeypatch, actual_archive, spell
):
    state, data, parameters, lots, level = prior._case(monkeypatch, spell)
    hero = state[f"{combined.SRD}.entities"]["hero"]
    hero["resources"][f"spell_slot_{level}"] = 1
    if spell in {"cure_wounds", "detect_evil_and_good"}:
        data["classes"] = [{"id": "wizard", "level": 11}, {"id": "cleric", "level": 6}]
        data["wizard_spellbook"].remove(spell)
        data["spellcasting_grants"][0]["spells"].remove(spell)
        data["spellcasting_grants"].append(
            {"source": "cleric", "ability": "wisdom", "spells": [spell]}
        )
    scene = state[f"{combined.SRD}.state"]["spatial_scene"]
    guard = next(row for row in scene["entities"] if row["id"] == "guard")
    guard["metadata"]["creature_type"] = "fiend"
    state[f"{combined.SRD}.entities"]["guard"]["creature_type"] = "fiend"
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / f"native-{spell}.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        actor = armed.state[f"{combined.SRD}.entities"]["hero"]
        source_id = actor["contingency_source_id"]
        assert actor["resources"][f"spell_slot_{level}"] == 0
        assert actor["concentration_spell_id"] is None
        before_resources = deepcopy(actor["resources"])
        quantities = {row["id"]: row["quantity"] for row in actor["spell_materials"]}
        before_speed = actor["speed"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == armed
        monkeypatch.setattr(dice, "_rng", Random(0))
        missile = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        } | {
            "spell": "magic_missile",
            "slot_resource": "spell_slot_2",
            "target": "hero",
            "material_item_ids": [],
        }
        hit, hit_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert hit.status is ActionStatus.COMMITTED, hit.result
        released = owner.snapshot("casting")
        lasting = released.state[f"{combined.SRD}.state"]
        actor = released.state[f"{combined.SRD}.entities"]["hero"]
        parent = lasting["contingency_sources"][source_id]
        assert parent["status"] == "released" and parent["installed"] is False
        assert parent["child_release_completed"] is True and parent["child_effect_applied"] is True
        assert parent["trigger_hp_before"] > parent["trigger_hp_after"]
        assert parent["child_cast_id"] != source_id
        expected_resources = dict(before_resources)
        expected_resources["spell_slot_2"] -= 1
        assert dict(actor["resources"]) == expected_resources
        assert {row["id"]: row["quantity"] for row in actor["spell_materials"]} == quantities
        if spell == "cure_wounds":
            assert parent["trigger_hp_after"] < actor["hp"] <= actor["max_hp"]
            assert actor["concentration_spell_id"] is None
            assert parent["release_summary"] == "Contingent family spell released on its caster."
        else:
            records = [row for row in lasting["turn_effects"] if row.get("spell") == spell]
            assert len(records) == 1 and records[0]["target"] == "hero"
            assert records[0]["expires_tick"] > armed.state[f"{combined.SRD}.state"]["lasting_tick"]
            if spell == "longstrider":
                assert actor["speed"] == before_speed + 10
                assert records[0]["cast_id"] == parent["child_cast_id"]
            else:
                assert actor["concentration_spell_id"] == spell
                query, query_command = _act(
                    owner, "query_srd_information_spell", caster_state="hero", spell=spell
                )
                assert query.status is ActionStatus.COMMITTED, query.result
                assert "fiend" in str(query.result).lower()
                released = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == released
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("A saved retry must not reroll."))
        for command, receipt in ((parent_command, parent_receipt), (hit_command, hit)):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("casting") == released
        if spell == "detect_evil_and_good":
            assert owner.execute_action(query_command) == query
            assert owner.snapshot("casting") == released
