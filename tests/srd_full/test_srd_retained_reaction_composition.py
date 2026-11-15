"""Paid retained attacks keep their real saved roll through final defensive completion."""

import json
from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_mirage_fall_composition as actual
from tests import test_srd_public_spell_casting as public
from tests import test_srd_retained_spell_attacks as source
from tests import test_srd_retained_spell_reactions as retained
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_produce_flame_reactions import _mage_spec
from tests.srd_full.test_srd_reaction_spells import _cast_parameters

actual_owners = actual.actual_owners
SRD = actual.SRD


@lru_cache
def _archive():
    if actual.SCOPE is None:
        return bundled_packages.bundled_srd_archive()
    primary, reference = actual.exact_payloads._linked_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [row for row in record["tools"] if row["name"] in actual.RUNTIME["tools"]]
    record["ui_surfaces"] = []
    name = "use_srd_vampiric_touch"
    if not any(row["name"] == name for row in record["tools"]):
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.spells",
                "input_schema": _tool_schema(actual.RUNTIME["tools"][name], tool_name=name),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
            }
        )
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(
            actual.RUNTIME["tools"][row["name"]], tool_name=row["name"]
        )
    names = {row["name"] for row in record["tools"]}
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": actual.ENCODED}
    declarations = json.loads(payload["content/content-declarations.json"])
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        if "tools" in links:
            links["tools"] = [tool for tool in links["tools"] if tool in names]
        if "ui_surfaces" in links:
            links["ui_surfaces"] = []
    if not any(name in row.get("links", {}).get("tools", []) for row in declarations["inclusions"]):
        declarations["inclusions"].append(
            {
                "id": "retained-reaction-composition-proof",
                "kind": "ruleset_runtime",
                "family": "retained_spell_attack",
                "disposition": "included_mechanical",
                "classifications": ["sylvia_gm_implementation"],
                "sources": [],
                "links": {
                    "artifacts": ["runtime/ruleset.yaml"],
                    "capabilities": ["dnd.srd.5.2.1.spells"],
                    "tools": [name],
                    "mechanics": [],
                    "procedures": [],
                    "prompt_fragments": [],
                    "schemas": [],
                    "state_namespaces": [],
                    "ui_surfaces": [],
                },
                "evidence": {"tests": ["paid-retained-reaction-reopen-retries"]},
            }
        )
    declarations["inclusions"].sort(key=lambda row: row["id"])
    payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
    return bundled_packages._archive(
        (NormalizedPackageDraft(record, payload, primary.format_version), reference)
    )


@pytest.fixture
def composed_owners(actual_owners, monkeypatch):
    monkeypatch.setattr(source, "_runtime", lambda: (actual.RUNTIME, actual.ENCODED))
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    observed = []
    execute = _effect_executor._execute_effect

    def observe(effect, context, executor=None):
        if context.current_tool == "d20_test" and "dice" in effect:
            observed.append(
                {
                    "kind": context.params.get("kind"),
                    "ability": context.params.get("ability"),
                    "actor": context.resolve_field("_actor_state_id"),
                }
            )
        return execute(effect, context, executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", observe)
    execute_tool = package_runtime.execute_tool

    def diagnose(name, parameters, *args, **kwargs):
        result = execute_tool(name, parameters, *args, **kwargs)
        if not result.success:
            print("Synthetic retained consequence:", name, result.failure_kind, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)
    return observed


def _case(mode):
    spec = _mage_spec()
    if mode == "fire":
        supplies, _, supplied = cards._spell_case("fire_shield")
        materials = supplies[f"{SRD}.entities"]["hero"]["spell_materials"]
        spec.update(
            creature_hp=1000,
            spellcasting_ability="intelligence",
            casting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            inventory=[row["inventory_item"] for row in materials],
            spell_materials=materials,
        )
        spec["spells"].append({"spell": "fire_shield", "uses_per_day": 2})
    else:
        supplied = None
    return (*retained._case(spec), supplied)


def _lasting(snapshot):
    return snapshot.state[f"{SRD}.state"]


def _actor(snapshot, actor="hero"):
    return snapshot.state[f"{SRD}.entities"][actor]


@pytest.mark.parametrize("initial", [True, False], ids=["initial", "later"])
@pytest.mark.parametrize(
    "mode,attack", [("shield", 5), ("shield", 20), ("mirror", 20), ("fire", 20)]
)
def test_paid_retained_defensive_window_reopens_and_completes_once(
    tmp_path, monkeypatch, composed_owners, initial, mode, attack
):
    state, data, parameters, supplies = _case(mode)
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "retained-final-reactions.db"
    receipts = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(
            owner, "vampiric_touch", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        if not initial:
            monkeypatch.setattr(dice, "_rng", source.Rolls(attack=1))
            receipts.append(actual._commit(owner, "cast_spell_outside_combat", **parameters))
            assert _actor(owner.snapshot("casting"))["resources"]["spell_slot_3"] == 2
            receipts.append(actual._commit(owner, "advance_srd_lasting_time", elapsed_rounds=1))
        if mode == "mirror":
            receipts.append(
                actual._commit(
                    owner,
                    "cast_spell_outside_combat",
                    spell="mirror_image",
                    caster_state="mage",
                    verbal_component_available=True,
                    somatic_component_available=True,
                )
            )
        if mode == "fire":
            receipts.append(
                actual._commit(
                    owner,
                    "cast_spell_outside_combat",
                    spell="fire_shield",
                    caster_state="mage",
                    target="mage",
                    origin_distance=0,
                    actor_spell_choice="warm",
                    verbal_component_available=True,
                    somatic_component_available=True,
                    material_component_available=True,
                    material_item_ids=supplies["material_item_ids"],
                )
            )
        before = owner.snapshot("casting")
        composed_owners.clear()
        rolled = source.Rolls(attack=attack)
        monkeypatch.setattr(dice, "_rng", rolled)
        if initial:
            begun, begin_command = actual._commit(owner, "begin_srd_spell_action", **parameters)
            pending = begun.result["context"]["_pending_action_id"]
            receipts.append((begun, begin_command))
            serial = _lasting(owner.snapshot("casting"))["pending_srd_action_sequence"]
            receipts.append(
                actual._commit(owner, "complete_srd_pending_action", pending_action_id=pending)
            )
            assert _lasting(owner.snapshot("casting"))["pending_srd_action_sequence"] == serial
        else:
            used, use_command = actual._commit(
                owner, "use_srd_vampiric_touch", caster="hero", caster_state="hero", target="mage"
            )
            pending = used.result["context"]["_pending_action_id"]
            receipts.append((used, use_command))
        saved = owner.snapshot("casting")
        event = deepcopy(_lasting(saved)["pending_srd_actions"][pending])
        assert event["kind"] == "spell_attack" and event["stage"] == "spell_attack_reaction"
        assert event["retained_initial"] is initial
        assert rolled.sides == [20]
        assert composed_owners == [
            {"kind": "attack_roll", "ability": "intelligence", "actor": "hero"}
        ]
        assert _actor(saved)["hp"] == _actor(before)["hp"]
        assert _actor(saved, "mage")["hp"] == _actor(before, "mage")["hp"]
        assert _actor(saved)["resources"]["spell_slot_3"] == 2
        sources = deepcopy(
            [
                row
                for row in _lasting(saved)["turn_effects"]
                if row.get("kind") == "retained_spell_attack"
            ]
        )
        assert len(sources) == 1 and sources[0]["cast_id"] == sources[0]["source_id"]
        assert sources[0]["source_caster_id"] == "hero"
        for result, command in receipts:
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        shield, shield_command = actual._commit(
            owner, "cast_srd_reaction_spell", **_cast_parameters("shield", pending)
        )
        reacted = owner.snapshot("casting")
        assert _actor(reacted, "mage")["spell_uses_remaining"]["shield"] == 1
        assert _actor(reacted)["hp"] == _actor(saved)["hp"]
        assert _actor(reacted, "mage")["hp"] == _actor(saved, "mage")["hp"]
        assert owner.execute_action(shield_command) == shield
        assert owner.snapshot("casting") == reacted
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == reacted
        continued = source.Rolls(attack=1, damage=6 if mode == "mirror" else 3)
        monkeypatch.setattr(dice, "_rng", continued)
        completed, completion_command = actual._commit(
            owner, "complete_srd_pending_action", pending_action_id=pending
        )
        finished = owner.snapshot("casting")
        context = completed.result["context"]
        assert context["_retained_attack"] == event["attack_roll"]
        assert context["_retained_armor_class"] == 17
        assert context["_retained_attack_kind"] == ("miss" if attack == 5 else "critical_hit")
        effective = 0 if attack == 5 or mode == "mirror" else 18
        assert context["_retained_necrotic_damage"] == effective
        assert context["_retained_healing_applied"] == effective // 2
        assert (
            _actor(before, "mage")["hp"]
            + _actor(before, "mage")["temp_hp"]
            - _actor(finished, "mage")["hp"]
            - _actor(finished, "mage")["temp_hp"]
            == effective
        )
        assert _actor(finished)["hp"] == _actor(before)["hp"] + effective // 2 - (
            2 if mode == "fire" else 0
        )
        assert _actor(finished)["resources"] == _actor(saved)["resources"]
        assert continued.sides.count(20) == (1 if mode == "fire" else 0)
        if mode == "fire":
            assert continued.sides.count(8) == 2
            assert composed_owners[-1] == {
                "kind": "saving_throw",
                "ability": "constitution",
                "actor": "hero",
            }
            assert len(composed_owners) == 2
            assert _actor(finished)["concentration_spell_id"] is None
        else:
            assert len(composed_owners) == 1
            assert [
                row
                for row in _lasting(finished)["turn_effects"]
                if row.get("kind") == "retained_spell_attack"
            ] == sources
        if mode == "mirror":
            assert context["_mirror_redirected"] is True
            assert continued.sides == [6, 6, 6]
        assert completed.result["result_card"]["title"] == "Vampiric Touch"
        assert _lasting(finished)["pending_srd_action_stack"] == []
        assert owner.execute_action(completion_command) == completed
        assert owner.snapshot("casting") == finished
        dice_after = list(continued.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == finished
        for result, command in receipts + [
            (shield, shield_command),
            (completed, completion_command),
        ]:
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == finished
        assert continued.sides == dice_after
