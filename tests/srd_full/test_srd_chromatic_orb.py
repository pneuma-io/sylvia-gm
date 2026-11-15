"""The real paid Orb roll earns a bounded same-action choice and a saved defensive window."""

import ast
import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_chromatic_orb as orb
from scripts import srd_reaction_spells as reactions
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE, _mage_spec
from tests.srd_full.test_srd_reaction_spells import _cast_parameters
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case

BASE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-chromatic-orb/ruleset.yaml")
)
RX = Path(__file__).parents[1] / ".scratch/chromatic-orb-parent-v1/scripts/srd_reaction_spells.py"


@lru_cache
def _runtime():
    assert sha256(BASE.read_bytes()).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    assert sha256(RX.read_bytes()).hexdigest() == (
        "70f1f2a9b391366d488301e46caeeb1c988bf8c1799550e134ec54b6756b5a3b"
    )
    original = BASE.read_text(encoding="utf-8")
    tree = ast.parse(RX.read_text(encoding="utf-8"))
    functions = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name
        in {
            "normalize_pending_spell_guards",
            "_strip_spell_hit_guard",
            "_spell_attack_hit_window",
        }
    ]
    namespace = dict(vars(reactions))
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(RX), "exec"), namespace)
    text = original
    # The retained parent has two historical nested copies; invert both through the real owner.
    for _ in range(2):
        text = namespace["normalize_pending_spell_guards"](text)
    text = orb.bind_paid_source(text)
    tools = yaml.load(text, Loader=yaml.CSafeLoader)["tools"]
    changed = {"cast_spell": deepcopy(tools["cast_spell"])}
    namespace["_spell_attack_hit_window"](tools, changed)
    text = _replace_tools(text, changed)
    text = orb.bind_runtime_consumers(text)
    assert orb.bind_runtime_consumers(text) == text
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    assert (
        runtime["tools"]["d20_test"]
        == yaml.load(original, Loader=yaml.CSafeLoader)["tools"]["d20_test"]
    )
    return text.encode(), runtime


@lru_cache
def _mechanic(name):
    encoded, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.action-economy",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


class _Rolls(Random):
    def __init__(self, attack=10, faces=(1, 1, 2)):
        super().__init__(0)
        self.attack = attack
        self.faces = iter(faces)
        self.sides = []

    def randint(self, _a, b):
        self.sides.append(b)
        return self.attack if b == 20 else next(self.faces) if b == 8 else 1


@pytest.fixture(autouse=True)
def native(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def test_real_matching_faces_at_zero_effective_damage_open_only_one_paid_choice(monkeypatch):
    state, data, params = _spell_case("chromatic_orb")
    state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["fire"]
    rolls = _Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = budget._run(
        "cast_spell", state, participant_data=data, **(params | {"selected_damage_type": "fire"})
    )
    assert result.result["status"] == "committed"
    assert rolls.sides == [20, 8, 8, 8]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 19
    lasting = state[f"{SRD}.state"]
    pending = lasting["pending_srd_action_stack"][-1]
    event = lasting["pending_srd_actions"][pending]
    assert event["owner_kind"] == "chromatic_orb" and event["stage"] == orb.CHOICE
    assert event["orb_source"]["visited"] == ["guard"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(Exception, match="same creature"):
        budget._run(
            "choose_srd_chromatic_orb_leap",
            state,
            caster_state="hero",
            pending_action_id=pending,
            target="guard",
            jump_distance=30,
            target_distance=5,
        )
    assert state == before and rolls.sides == [20, 8, 8, 8]
    # A saved clock/turn change can close this already-paid choice, but cannot roll a late leap.
    state[f"{SRD}.state"]["lasting_tick"] = 2
    state[f"{SRD}.combat"]["active_actor"] = "other"
    budget._run(
        "choose_srd_chromatic_orb_leap",
        state,
        caster_state="hero",
        pending_action_id=pending,
        target="other",
        jump_distance=30,
        target_distance=35,
    )
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
    assert rolls.sides == [20, 8, 8, 8]


@pytest.mark.parametrize(
    "attack,faces,sides", [(1, (1, 1, 2), [20]), (10, (1, 2, 3), [20, 8, 8, 8])]
)
def test_native_miss_or_unmatched_damage_faces_cannot_earn_a_leap(
    monkeypatch, attack, faces, sides
):
    state, data, params = _spell_case("chromatic_orb")
    rolls = _Rolls(attack=attack, faces=faces)
    monkeypatch.setattr(dice, "_rng", rolls)
    budget._run(
        "cast_spell", state, participant_data=data, **(params | {"selected_damage_type": "thunder"})
    )
    assert not state[f"{SRD}.state"].get("pending_srd_action_stack")
    assert rolls.sides == sides


def test_upcast_critical_faces_allow_two_new_rolls_then_stop_at_the_actual_slot(monkeypatch):
    from sylvia_gm.runtime import _effect_executor as ee

    original_call = ee._EFFECT_HANDLERS["call"]
    receipts = []

    def traced(body, effect, context, executor):
        def observe(name, parameters, parent):
            result = executor(name, parameters, parent)
            if name == orb.FINISH:
                receipts.append(
                    (result.context["_orb_source"]["hops"], result.context["_orb_same_d8"])
                )
            return result

        return original_call(body, effect, context, observe)

    monkeypatch.setitem(ee._EFFECT_HANDLERS, "call", traced)
    state, data, params = _spell_case("chromatic_orb")
    state[f"{SRD}.entities"]["third"] = deepcopy(state[f"{SRD}.entities"]["other"])
    state[f"{SRD}.entities"]["third"]["name"] = "Third"
    rolls = _Rolls(attack=20, faces=(1,) * 24)
    monkeypatch.setattr(dice, "_rng", rolls)
    budget._run(
        "cast_spell",
        state,
        participant_data=data,
        **(
            params
            | {
                "slot_resource": "spell_slot_2",
                "selected_damage_type": "cold",
                "target_distance": 90,
            }
        ),
    )
    lasting = state[f"{SRD}.state"]
    pending = lasting["pending_srd_action_stack"][-1]
    for target, distance in (("other", 120), ("third", 150)):
        assert state[f"{SRD}.state"]["pending_srd_action_stack"] == [pending], target
        budget._run(
            "choose_srd_chromatic_orb_leap",
            state,
            caster_state="hero",
            pending_action_id=pending,
            target=target,
            jump_distance=30,
            target_distance=distance,
        )
        assert state[f"{SRD}.state"]["pending_srd_actions"][pending]["stage"] == (
            "spell_attack_reaction"
        )
        before = len(rolls.sides)
        result = budget._run("complete_srd_pending_action", state, pending_action_id=pending)
        assert result.result["context"]["_spell_attack_kind"] == "critical_hit"
        assert rolls.sides[before:] == [8] * 8
    assert rolls.sides == ([20] + [8] * 8) * 3
    assert receipts == [(1, True), (2, True)]
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2


@lru_cache
def _archive():
    encoded, runtime = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [row for row in record["tools"] if row["name"] in runtime["tools"]]
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(runtime["tools"][declaration["name"]])
    name = "choose_srd_chromatic_orb_leap"
    record["tools"] = [row for row in record["tools"] if row["name"] != name]
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
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    for inclusion in content["inclusions"]:
        links = inclusion.get("links", {})
        links["tools"] = [name for name in links.get("tools", []) if name in runtime["tools"]]
    content["inclusions"].append(
        {
            "id": "chromatic-orb-earned-choice",
            "kind": "ruleset_runtime",
            "family": "spells",
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
            "evidence": {"tests": ["orb-paid-leap-shield-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda row: row["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_paid_public_leap_shield_reopen_and_retry_preserve_one_action_and_each_roll(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    rolls = _Rolls(attack=5)
    monkeypatch.setattr(dice, "_rng", rolls)
    database = tmp_path / "orb.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "chromatic_orb")
        prepared, _ = _act(owner, "prepare_srd_creature", **_mage_spec())
        assert prepared.status is ActionStatus.COMMITTED
        paid, _ = _act(
            owner,
            "cast_spell_outside_combat",
            **(params | {"selected_damage_type": "fire", "target_distance": 90}),
        )
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        pending = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        chosen, choose_command = _act(
            owner,
            "choose_srd_chromatic_orb_leap",
            caster_state="hero",
            pending_action_id=pending,
            target="mage",
            jump_distance=30,
            target_distance=120,
        )
        assert chosen.status is ActionStatus.COMMITTED, chosen.result
        assert "awaiting defense" in str(chosen.result["result_card"])
        with pytest.raises(MechanicsRejected):
            _act(owner, "cast_srd_reaction_spell", **_cast_parameters("counterspell", pending))
        shield, _ = _act(owner, "cast_srd_reaction_spell", **_cast_parameters("shield", pending))
        assert shield.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(choose_command) == chosen
        finished, finish_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=pending
        )
        assert finished.status is ActionStatus.COMMITTED, finished.result
        assert finished.result["context"]["_spell_attack_kind"] == "miss"
        final = owner.snapshot("casting")
        assert rolls.sides == [20, 8, 8, 8, 20]
        assert final.state[f"{SRD}.entities"]["mage"]["hp"] == 1000
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert not final.state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert owner.execute_action(finish_command) == finished
        assert owner.snapshot("casting") == final
