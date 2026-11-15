"""Saved list targeting retains Sanctuary decisions without repeating completed saves."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import srd_sanctuary_lists as sanctuary
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_reaction_spells import _mage
from tests.srd_full.test_srd_scene_relations import _state as scene_state

SOURCE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-sanctuary-lists/dnd-srd-5.2.1")
)


@lru_cache
def _runtime():
    text = sanctuary.bind_runtime_consumers((SOURCE / "runtime/ruleset.yaml").read_text())
    assert sanctuary.bind_runtime_consumers(text) == text
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


class Rolls:
    def __init__(self, values=()):
        self.values = list(values)
        self.d20s = 0

    def randint(self, _a, sides):
        if sides == 20:
            self.d20s += 1
            return self.values.pop(0) if self.values else 10
        return 1


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())


def _parameters(spell):
    params = {
        "spell": spell,
        "caster_state": "mage",
        "verbal_component_available": True,
        "somatic_component_available": True,
        "targets_max_distance": 20,
    }
    if spell == "magic_missile":
        params.update(
            target="guard",
            target_distance=5,
            caster_can_see_target=True,
            projectile_targets=["guard", "other", "guard"],
            all_targets_have_clear_path=True,
            caster_can_see_all_targets=True,
        )
    else:
        params.update(
            attack_targets=["guard", "guard", "other"], all_attack_targets_have_clear_path=True
        )
    return params


def test_list_admission_before_action_and_scene_retarget_atomicity(monkeypatch):
    state, data, params = cards._spell_case("sanctuary")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _mage(state, "magic_missile")
    state[f"{SRD}.entities"]["mage"]["saving_throw_modifiers"]["wisdom"] = -10
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="every projectile"):
        _run(
            "begin_srd_spell_action",
            state,
            **(_parameters("magic_missile") | {"projectile_targets": ["guard", "other"]}),
        )
    assert state == before
    result = _run("begin_srd_spell_action", state, **_parameters("magic_missile"))
    event_id = result.result["context"]["_pending_action_id"]
    _run(
        "decide_srd_sanctuary_target",
        state,
        pending_action_id=event_id,
        decision="retarget",
        new_target_state_id="other",
    )
    scene = deepcopy(scene_state()[f"{SRD}.state"]["spatial_scene"])
    for actor, position in (("mage", [0, 0, 0]), ("other", [130, 0, 0])):
        row = deepcopy(scene["entities"][0])
        row.update(id=actor, position=position)
        row["metadata"]["participant_ref"] = actor
        scene["entities"].append(row)
    scene["map"]["bounds"]["max"][0] = 200
    state[f"{SRD}.state"]["spatial_scene"] = scene
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="source range"):
        _run(
            "resume_srd_sanctuary_spell",
            state,
            pending_action_id=event_id,
            replacement_target_distance=5,
            replacement_caster_can_see_target=True,
            replacement_has_clear_path=True,
        )
    assert state == before
    state[f"{SRD}.state"]["spatial_scene"]["entities"][-1]["position"] = [20, 0, 0]
    _run("resume_srd_sanctuary_spell", state, pending_action_id=event_id)
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    assert event["parameters"]["projectile_targets"] == ["other", "other", "other"]
    assert len(event["sanctuary_saves"]) == 1


def _archive():
    primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    runtime, encoded = _runtime()
    for declaration in record["tools"]:
        if declaration["name"] in {"resume_srd_sanctuary_spell"}:
            declaration["input_schema"] = _tool_schema(
                runtime["tools"][declaration["name"]], tool_name=declaration["name"]
            )
    return bundled_packages._archive(
        (
            NormalizedPackageDraft(
                record,
                dict(primary.payloads) | {"runtime/ruleset.yaml": encoded},
                primary.format_version,
            ),
            reference,
        )
    )


@pytest.mark.parametrize("spell", ["magic_missile", "scorching_ray"])
def test_public_list_decisions_reopen_paid_completion_and_retry(tmp_path, monkeypatch, spell):
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: SOURCE)
    monkeypatch.setattr(public, "ROOT", SOURCE.parents[1])
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    rolls = Rolls([1, 1, 20, 20, 10, 10] if spell == "scorching_ray" else [])
    monkeypatch.setattr(dice, "_rng", rolls)
    database = tmp_path / f"{spell}.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "sanctuary", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        assert (
            _act(owner, "cast_spell_outside_combat", **params)[0].status is ActionStatus.COMMITTED
        )
        if spell in {"magic_missile", "scorching_ray"}:
            assert (
                _act(owner, "cast_spell_outside_combat", **(params | {"target": "other"}))[0].status
                is ActionStatus.COMMITTED
            )
        assert (
            _prepare_mage(
                owner,
                [{"spell": spell, "uses_per_day": 2}],
                wisdom_save=(3 if spell == "scorching_ray" else -10),
            )[0].status
            is ActionStatus.COMMITTED
        )
        start, start_command = _act(owner, "begin_srd_spell_action", **_parameters(spell))
        assert start.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        event_id = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        event = saved.state[f"{SRD}.state"]["pending_srd_actions"][event_id]
        assert event["stage"] == "awaiting_sanctuary_choice"
        assert len(event["sanctuary_saves"]) == 1
        assert owner.execute_action(start_command) == start
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(start_command) == start
        if spell == "scorching_ray":
            lost, lost_command = _act(
                owner,
                "decide_srd_sanctuary_target",
                pending_action_id=event_id,
                decision="lose_operation",
            )
            assert lost.status is ActionStatus.COMMITTED
            event = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_actions"][event_id]
            assert event["sanctuary_lost_rays"] == [0]
            assert [s["operation_index"] for s in event["sanctuary_saves"]] == [0, 1]
            assert owner.execute_action(lost_command) == lost
        for replacement in ["hero", "hero"] if spell == "magic_missile" else ["other"]:
            choice, choice_command = _act(
                owner,
                "decide_srd_sanctuary_target",
                pending_action_id=event_id,
                decision="retarget",
                new_target_state_id=replacement,
            )
            assert choice.status is ActionStatus.COMMITTED
            assert owner.execute_action(choice_command) == choice
            facts = {
                "replacement_target_distance": 20,
                "replacement_caster_can_see_target": True,
                "replacement_has_clear_path": True,
            }
            if spell == "scorching_ray":
                facts.update(replacement_cover="none", replacement_target_can_see_caster=True)
            resumed, resume_command = _act(
                owner, "resume_srd_sanctuary_spell", pending_action_id=event_id, **facts
            )
            assert resumed.status is ActionStatus.COMMITTED
            assert owner.execute_action(resume_command) == resumed
        before = owner.snapshot("casting")
        event = before.state[f"{SRD}.state"]["pending_srd_actions"][event_id]
        assert event["stage"] == "reaction"
        assert len(event["sanctuary_saves"]) == (4 if spell == "scorching_ray" else 2)
        if spell == "scorching_ray":
            assert [s["operation_index"] for s in event["sanctuary_saves"]] == [0, 1, 1, 2]
            assert [s["outcome"] for s in event["sanctuary_saves"]] == [
                "failure",
                "failure",
                "success",
                "success",
            ]
        if spell == "magic_missile":
            assert event["parameters"]["projectile_targets"] == ["hero"] * 3
        else:
            assert event["parameters"]["attack_targets"] == ["guard", "other", "other"]
        rolls_before = rolls.d20s
        done, done_command = _act(owner, "complete_srd_pending_action", pending_action_id=event_id)
        assert done.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert after.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][spell] == 1
        target = "hero" if spell == "magic_missile" else "other"
        assert (
            after.state[f"{SRD}.entities"][target]["hp"]
            < before.state[f"{SRD}.entities"][target]["hp"]
        )
        assert rolls.d20s - rolls_before == (0 if spell == "magic_missile" else 2)
        assert after.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert owner.execute_action(done_command) == done
        assert owner.snapshot("casting").state == after.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == after.state
        assert owner.execute_action(done_command) == done
        assert owner.snapshot("casting").state == after.state
