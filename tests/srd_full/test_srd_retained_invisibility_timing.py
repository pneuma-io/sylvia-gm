"""The genuine retained Vampiric Touch owner observes living sources through its real D20."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_invisibility_completion_timing as timing
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_npc_spell_casting import _act

real_owners = timing.real_owners


@lru_cache
def _archive():
    if timing.OUTPUT is None:
        return bundled_packages.bundled_srd_archive()
    primary, reference = timing.exact_payloads._linked_drafts()
    record = deepcopy(primary.record)
    runtime = timing.RUNTIME
    record["tools"] = [r for r in record["tools"] if r["name"] in runtime["tools"]]
    record["ui_surfaces"] = []
    for declaration in record["tools"]:
        if declaration["name"] in {
            "cast_spell",
            "cast_spell_outside_combat",
            "begin_srd_spell_action",
        }:
            declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
    name = "use_srd_vampiric_touch"
    record["tools"] = [d for d in record["tools"] if d["name"] != name]
    record["tools"].append(
        {
            "name": name,
            "capability": "dnd.srd.5.2.1.spells",
            "input_schema": _tool_schema(runtime["tools"][name]),
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
        }
    )
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": timing.ENCODED}
    declarations = json.loads(payload["content/content-declarations.json"])
    names = {r["name"] for r in record["tools"]}
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        if "tools" in links:
            links["tools"] = [n for n in links["tools"] if n in names]
        if "ui_surfaces" in links:
            links["ui_surfaces"] = []
    # This is the exact frozen retained owner's proposed public declaration, not a
    # substitute casting or damage tool. Actual BUILD uses its admitted declaration.
    declarations["inclusions"].append(
        {
            "id": "retained-invisibility-timing-proof",
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
            "evidence": {"tests": ["retained-invisibility-real-public-rolls"]},
        }
    )
    declarations["inclusions"].sort(key=lambda row: row["id"])
    payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
    archive = bundled_packages._archive(
        (NormalizedPackageDraft(record, payload, primary.format_version), reference)
    )
    (timing.OUTPUT / (timing.LABEL + "-retained-public-package.zip")).write_bytes(archive)
    return archive


@pytest.fixture(autouse=True)
def actual_retained_public_owner(real_owners, monkeypatch):
    monkeypatch.setattr(timing.public, "bundled_srd_archive", _archive)
    observations = []
    original = _effect_executor._execute_effect

    def observe(effect, context, executor=None):
        if (
            context.current_tool == "d20_test"
            and context.params.get("kind") == "attack_roll"
            and "dice" in effect
        ):
            observations.append(
                {
                    "actor": context.resolve_field("_actor_state_id"),
                    "conditions": deepcopy(context.resolve_field("actor_state.conditions")),
                    "ordinary_source_index": context.resolve_field(
                        "actor_state.illumination_has_invisibility"
                    ),
                }
            )
        return original(effect, context, executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", observe)
    return observations


def _records(state):
    return [
        r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("kind") == "retained_spell_attack"
    ]


@pytest.mark.parametrize(
    "source,native", [("invisibility", False), ("greater_invisibility", False), (None, True)]
)
def test_initial_paid_and_later_retained_rolls_keep_live_unseen_source_then_end_only_ordinary(
    source, native, tmp_path, monkeypatch, actual_retained_public_owner
):
    case = timing._case("vampiric_touch", source=source, native=native)
    case[0][f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    monkeypatch.setattr(timing.public, "_spell_case", lambda _: case)
    database = tmp_path / "retained-live-source.db"
    original_advantage = dice.advantage
    rolls = []

    def observe_advantage(modifier):
        result = original_advantage(modifier)
        rolls.append(result.to_dict())
        return result

    monkeypatch.setattr(dice, "advantage", observe_advantage)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        timing.public._create_spell_campaign(
            owner, "vampiric_touch", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        before = owner.snapshot("casting")
        greater_sources = deepcopy(timing._source_records(before.state, "greater_invisibility"))
        monkeypatch.setattr(dice, "_rng", Random(2))
        paid, paid_command = _act(owner, "cast_spell_outside_combat", **case[2])
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        assert len(rolls) == len(actual_retained_public_owner) == 1
        assert actual_retained_public_owner[0]["actor"] == "hero"
        assert "invisible" in actual_retained_public_owner[0]["conditions"]
        assert ("invisible" in saved.state[f"{SRD}.entities"]["hero"]["conditions"]) is (
            source != "invisibility"
        )
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        record = deepcopy(_records(saved.state)[0])
        assert record["cast_id"] == record["source_id"]
        assert record["caster"] == record["source_caster_id"] == "hero"
        assert "miss" in str(paid.result["result_card"])
        assert owner.execute_action(paid_command) == paid
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        advanced, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        assert advanced.status is ActionStatus.COMMITTED, advanced.result
        if source == "invisibility":
            hidden = owner.snapshot("casting").state[f"{SRD}.entities"]["hidden"]
            renewed, _ = _act(
                owner,
                "cast_spell_outside_combat",
                spell="invisibility",
                caster_state="hidden",
                target="hero",
                verbal_component_available=True,
                somatic_component_available=True,
                material_component_available=True,
                material_item_ids=[m["id"] for m in hidden["spell_materials"]],
            )
            assert renewed.status is ActionStatus.COMMITTED, renewed.result
        assert (
            "invisible" in owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["conditions"]
        )
        monkeypatch.setattr(dice, "_rng", Random(2))
        later, later_command = _act(
            owner, "use_srd_vampiric_touch", caster="hero", caster_state="hero", target="guard"
        )
        assert later.status is ActionStatus.COMMITTED, later.result
        final = owner.snapshot("casting")
        assert len(rolls) == len(actual_retained_public_owner) == 2
        assert all(
            r["actor"] == "hero" and "invisible" in r["conditions"]
            for r in actual_retained_public_owner
        )
        if source == "invisibility":
            assert all(r["ordinary_source_index"] is True for r in actual_retained_public_owner)
        assert timing._source_records(final.state, "greater_invisibility") == greater_sources
        assert all(len(r["results"]) == 2 and r["total"] == max(r["results"]) for r in rolls)
        assert ("invisible" in final.state[f"{SRD}.entities"]["hero"]["conditions"]) is (
            source != "invisibility"
        )
        assert _records(final.state) == [record]
        assert (
            final.state[f"{SRD}.entities"]["hero"]["resources"]
            == saved.state[f"{SRD}.entities"]["hero"]["resources"]
        )
        assert (
            final.state[f"{SRD}.entities"]["guard"]["hp"]
            == before.state[f"{SRD}.entities"]["guard"]["hp"]
        )
        assert final.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "vampiric_touch"
        assert owner.execute_action(later_command) == later
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert owner.execute_action(paid_command) == paid
        assert owner.execute_action(later_command) == later
        assert len(rolls) == len(actual_retained_public_owner) == 2
        assert owner.snapshot("casting") == final


@pytest.mark.parametrize("seen", [False, True], ids=["unseen-rejects", "actual-see-countered"])
def test_retained_initial_pending_source_and_counterspell_lost_completion(
    seen, tmp_path, monkeypatch, actual_retained_public_owner
):
    case = timing._case("vampiric_touch", see=seen)
    database = tmp_path / "retained-counterspell.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        timing._campaign(owner, monkeypatch, "vampiric_touch", case)
        begun, begin_command = _act(owner, "begin_srd_spell_action", **case[2])
        assert begun.status is ActionStatus.COMMITTED, begun.result
        saved = owner.snapshot("casting")
        event = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert "invisible" in saved.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert _records(saved.state) == []
        assert owner.execute_action(begin_command) == begun
        monkeypatch.setattr(dice, "_rng", Random(2))
        if seen:
            countered, counter_command = _act(
                owner,
                "cast_srd_reaction_spell",
                spell="counterspell",
                caster_state="mage",
                reaction_trigger_id=event,
                somatic_component_available=True,
            )
            assert countered.status is ActionStatus.COMMITTED, countered.result
            saved = owner.snapshot("casting")
            assert saved.state[f"{SRD}.state"]["pending_srd_actions"][event]["status"] == "lost"
            assert "invisible" in saved.state[f"{SRD}.entities"]["hero"]["conditions"]
            assert owner.execute_action(counter_command) == countered
        else:
            with pytest.raises(MechanicsRejected) as rejected:
                _act(
                    owner,
                    "cast_srd_reaction_spell",
                    spell="counterspell",
                    caster_state="mage",
                    reaction_trigger_id=event,
                    somatic_component_available=True,
                )
            assert rejected.value.state_changed is False
            assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        completed, command = _act(owner, "complete_srd_pending_action", pending_action_id=event)
        assert completed.status is ActionStatus.COMMITTED, completed.result
        final = owner.snapshot("casting")
        assert "invisible" not in final.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == (
            3 if seen else 2
        )
        assert bool(_records(final.state)) is (not seen)
        assert len(actual_retained_public_owner) == (0 if seen else 1)
        assert owner.execute_action(command) == completed
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert owner.execute_action(command) == completed
        assert len(actual_retained_public_owner) == (0 if seen else 1)
