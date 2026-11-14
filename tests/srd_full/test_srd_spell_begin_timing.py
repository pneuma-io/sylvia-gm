"""Accepted casting uses actual Slow rolls, saved sources and one owning completion."""

import hashlib
import importlib.util
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_extended_casting import _external_slow
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_reaction_spells import _mage
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene

ROOT = Path(
    os.environ.get(
        "FC14_BEGIN_RUNTIME_ROOT",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-begin-runtime-root"),
    )
)
LIGHT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-spell-begin-timing/input-1")
)


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


timing = _module(
    "scripts._tested_spell_begin_timing",
    Path(__file__).parents[1] / "scripts/srd_spell_begin_timing.py",
)
assert (
    hashlib.sha256((LIGHT / "scripts/srd_invisibility_cast_consumers.py").read_bytes()).hexdigest()
    == "605e59b361fefde58980f01a2ca36a8bf3118b127c4ff991820594053556e694"
)
light = _module(
    "scripts._exact_light12_cast_consumers", LIGHT / "scripts/srd_invisibility_cast_consumers.py"
)


@lru_cache
def _runtime(bound=True):
    payload = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    expected = os.environ.get(
        "FC14_BEGIN_RUNTIME_SHA", "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    assert hashlib.sha256(payload).hexdigest() == expected
    # No family regeneration: these two final binders consume genuine compiled tools.
    text = light.bind_runtime_consumers(payload.decode().replace("\r\n", "\n"))
    if bound:
        text = timing.bind_runtime_consumers(text)
    return _load_runtime(text), text.encode()


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


@pytest.fixture(autouse=True)
def actual_owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    slow_rolls = []
    execute = _effect_executor._execute_effect

    def observe(effect, context, tool_executor=None):
        if "dice" in effect and context.current_tool == "resolve_srd_slow_cast_failure":
            slow_rolls.append(effect)
        return execute(effect, context, tool_executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", observe)
    return slow_rolls


def _case(spell="false_life", *, slow=True, invisible="invisibility", native=False):
    state, data, params = cards._spell_case(spell)
    combat = deepcopy(state[f"{SRD}.combat"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero"),
            _entity("mage", [0, 5, 0], participant_ref="mage"),
            _entity("guard", [5, 0, 0], participant_ref="guard"),
            _entity("other", [0, 10, 0], participant_ref="other"),
            _entity("hidden", [0, -5, 0], participant_ref="hidden"),
        ),
    }
    if slow:
        _external_slow(state, data)
    if native:
        state[f"{SRD}.entities"]["hero"]["conditions"].append("invisible")
    if invisible:
        materials = cards._spell_case("invisibility")[0][f"{SRD}.entities"]["hero"][
            "spell_materials"
        ]
        _run(
            "prepare_srd_creature",
            state,
            creature_id="hidden",
            name="Independent invisible caster",
            source="Synthetic caster: AC12 HP30 Speed30 Int3 attack7 DC15.",
            armor_class=12,
            hit_points=30,
            speed=30,
            initiative_modifier=-10,
            size_rank=2,
            constitution_save=0,
            spellcasting_ability="intelligence",
            spellcasting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            inventory=[m["inventory_item"] for m in materials],
            spell_materials=materials,
            spells=[{"spell": invisible, "uses_per_day": 3}],
        )
        _run(
            "cast_spell_outside_combat",
            state,
            spell=invisible,
            caster_state="hidden",
            target="hero",
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
            material_item_ids=[m["id"] for m in materials],
        )
        assert "invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]
    state[f"{SRD}.combat"] = combat
    return state, data, params


def _invisible(state):
    return "invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]


@pytest.mark.parametrize(
    "invisible,native", [("greater_invisibility", False), ("invisibility", True)]
)
@pytest.mark.parametrize("seed", [0, 2], ids=["slow-success", "slow-failure"])
def test_accepted_slow_begin_preserves_greater_or_independent_native_invisible(
    monkeypatch, invisible, native, seed
):
    state, data, params = _case(invisible=invisible, native=native)
    monkeypatch.setattr(dice, "_rng", Random(seed))
    result = _run("begin_srd_spell_action", state, participant_data=data, **params).result
    assert result["context"]["_slow_cast_failure_roll"]["results"] == ([4] if seed == 0 else [1])
    assert _invisible(state)
    records = state[f"{SRD}.state"]["turn_effects"]
    if native:
        assert not any(record.get("spell") == "invisibility" for record in records)
    else:
        assert any(record.get("spell") == "greater_invisibility" for record in records)
    if seed == 0:
        event = state[f"{SRD}.state"]["pending_srd_actions"][
            result["context"]["_pending_action_id"]
        ]
        assert event["slow_cast"]["failed"] is False
        assert event["slow_cast"]["spell"] == params["spell"]
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 3
    else:
        assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert "Casting failed" in str(result["result_card"])


def test_rejected_components_leave_invisible_sources_action_and_resources_unchanged():
    state, data, params = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Somatic"):
        _run(
            "begin_srd_spell_action",
            state,
            participant_data=data,
            **(params | {"somatic_component_available": False}),
        )
    assert state == before


@pytest.mark.parametrize("somatic_exempt", [False, True])
def test_actual_slow_npc_failure_pays_one_use_and_source_exemption_caches_no_roll(
    monkeypatch, somatic_exempt, actual_owner
):
    state, data, _ = _case(slow=False, invisible=None)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("npc", [-5, 5, 0], participant_ref="npc")
    )
    _run(
        "prepare_srd_creature",
        state,
        creature_id="npc",
        name="Somatic test caster",
        source=(
            "Synthetic caster: AC12 HP30 Speed30 Int3 attack7 DC15 Wisdom save0; "
            "source Somatic exemption when selected."
        ),
        armor_class=12,
        hit_points=30,
        speed=30,
        initiative_modifier=20,
        size_rank=2,
        constitution_save=0,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spell_component_exemptions=["somatic"] if somatic_exempt else [],
        spells=[{"spell": "fire_bolt", "uses_per_day": 2}],
    )
    state[f"{SRD}.entities"]["npc"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    _mage(state, "slow")
    slow_state, _, slow_params = cards._spell_case("slow")
    supplies = slow_state[f"{SRD}.entities"]["hero"]["spell_materials"]
    state[f"{SRD}.entities"]["mage"].update(
        spell_materials=supplies,
        inventory=[m["inventory_item"] for m in supplies],
    )
    slow_params.pop("caster")
    slow_params.pop("slot_resource")
    slow_params.update(caster_state="mage", targets=["npc"], forego_save_targets=["npc"])
    _run("cast_spell_outside_combat", state, **slow_params)
    assert state[f"{SRD}.entities"]["npc"]["spell_slow"] is True
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = 0
    _run(
        "start_combat_encounter_entities",
        state,
        participant_data=data,
        first_actor="hero",
        second_actor="npc",
        first_surprised=False,
        second_surprised=False,
        tie_winner="second",
    )
    _run("begin_combat_turn", state, participant_data=data)
    assert state[f"{SRD}.combat"]["active_actor"] == "npc"
    monkeypatch.setattr(dice, "_rng", Random(2))
    result = _run(
        "begin_srd_spell_action",
        state,
        participant_data=data,
        spell="fire_bolt",
        caster_state="npc",
        target="guard",
        verbal_component_available=True,
        somatic_component_available=not somatic_exempt,
    ).result
    npc = state[f"{SRD}.entities"]["npc"]
    if somatic_exempt:
        assert result["context"]["_slow_cast_failure_roll"] is None
        event = state[f"{SRD}.state"]["pending_srd_actions"][
            result["context"]["_pending_action_id"]
        ]
        assert "somatic" not in event["components"]
        assert event["slow_cast"]["roll"] is None
        assert event["slow_cast"]["failed"] is False
        assert npc["spell_uses_remaining"]["fire_bolt"] == 2
        assert not actual_owner
    else:
        assert result["context"]["_slow_cast_failure_roll"]["results"] == [1]
        assert npc["spell_uses_remaining"]["fire_bolt"] == 1
        assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert "Casting failed" in str(result["result_card"])
        assert len(actual_owner) == 1


def test_failed_slow_feature_cast_spends_actual_magic_initiate_pool_once(monkeypatch):
    state, data, params = _case("cure_wounds")
    data.update(
        level=1,
        classes=[{"id": "fighter", "level": 1}],
        origin_feats=["magic_initiate"],
        spellcasting_grants=[
            {"source": "magic_initiate_cleric", "ability": "wisdom", "spells": ["cure_wounds"]}
        ],
    )
    params["slot_resource"] = "feature"
    state[f"{SRD}.entities"]["hero"].update(resources={}, resource_maxima={})
    target_hp = state[f"{SRD}.entities"]["guard"]["hp"]
    monkeypatch.setattr(dice, "_rng", Random(2))
    result = _run("begin_srd_spell_action", state, participant_data=data, **params).result
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["spell_grant_uses"]["magic_initiate_cleric"] == 1
    assert hero["resources"] == {}
    assert state[f"{SRD}.entities"]["guard"]["hp"] == target_hp
    assert not _invisible(state)
    assert "Feature uses spent" in str(result["result_card"])
    assert "Casting failed" in str(result["result_card"])


def test_original_native_begin_defers_slow_and_invisibility_while_final_binder_settles_them(
    monkeypatch,
):
    state, data, params = _case()
    original_state = deepcopy(state)
    runtime, encoded = _runtime(False)
    original = materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name="begin_srd_spell_action",
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(
                _tool_schema(
                    runtime["tools"]["begin_srd_spell_action"], tool_name="begin_srd_spell_action"
                )
            ),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )
    with monkeypatch.context() as local:
        local.setattr(budget, "_mechanic", lambda _name: original)
        before = _run(
            "begin_srd_spell_action", original_state, participant_data=data, **params
        ).result
    event = original_state[f"{SRD}.state"]["pending_srd_actions"][
        before["context"]["_pending_action_id"]
    ]
    assert event.get("slow_cast") is None
    assert _invisible(original_state)
    monkeypatch.setattr(dice, "_rng", Random(0))
    after = _run("begin_srd_spell_action", state, participant_data=data, **params).result
    assert after["context"]["_slow_cast_failure_roll"]["results"] == [4]
    assert not _invisible(state)


def test_final_binder_preserves_public_schema_completion_owner_and_acyclic_graph():
    before, _ = _runtime(False)
    after, encoded = _runtime()
    assert timing.bind_runtime_consumers(encoded.decode()).encode() == encoded
    assert before["definitions"] == after["definitions"]
    assert (
        before["tools"]["complete_srd_pending_action"]
        == after["tools"]["complete_srd_pending_action"]
    )
    assert before["tools"]["cast_spell"] == after["tools"]["cast_spell"]
    assert _tool_schema(
        before["tools"]["begin_srd_spell_action"], tool_name="begin_srd_spell_action"
    ) == _tool_schema(after["tools"]["begin_srd_spell_action"], tool_name="begin_srd_spell_action")
    graph = {
        name: {n["call"] for n in timing._walk(tool["effects"]) if isinstance(n.get("call"), str)}
        for name, tool in after["tools"].items()
    }
    done, active = set(), set()

    def visit(name):
        assert name not in active, name
        if name in done:
            return
        active.add(name)
        for child in graph[name]:
            visit(child)
        active.remove(name)
        done.add(name)

    for name in graph:
        visit(name)


@lru_cache
def _archive():
    with patch.object(
        bundled_packages, "_bundled_srd_root", lambda: ROOT / "rulesets/dnd-srd-5.2.1"
    ):
        primary, reference = bundled_packages._srd_drafts()
    runtime, encoded = _runtime()
    record = deepcopy(primary.record)
    declaration = next(row for row in record["tools"] if row["name"] == "begin_srd_spell_action")
    declaration["input_schema"] = _tool_schema(
        runtime["tools"]["begin_srd_spell_action"], tool_name="begin_srd_spell_action"
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


class _Draws(Random):
    def __init__(self, seed):
        super().__init__(seed)
        self.draws = []

    def randint(self, a, b):
        result = super().randint(a, b)
        self.draws.append((a, b, result))
        return result


@pytest.mark.parametrize("mode", ["inactive", "success", "failure"])
def test_paid_public_begin_saved_decision_reopen_retry_and_one_owning_completion(
    tmp_path, monkeypatch, mode, actual_owner
):
    spell = "stoneskin" if mode == "failure" else "false_life"
    state, data, params = _case(spell, slow=mode != "inactive")
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public, "ROOT", ROOT)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: (state, data, params))
    database = tmp_path / "accepted-begin.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, spell)
        # Actual initiative and turn owners make Hero a legal casting actor.
        _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="hero",
            second_actor="other",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        _act(owner, "begin_combat_turn")
        before = owner.snapshot("casting")
        assert before.state[f"{SRD}.combat"]["active_actor"] == "hero"
        rng = _Draws(2 if mode == "failure" else 0)
        monkeypatch.setattr(dice, "_rng", rng)
        receipt, command = _act(owner, "begin_srd_spell_action", **params)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
        assert len(rng.draws) == (0 if mode == "inactive" else 1)
        assert len(actual_owner) == (0 if mode == "inactive" else 1)
        level = params["slot_resource"]
        hero = saved.state[f"{SRD}.entities"]["hero"]
        if mode == "failure":
            assert not _invisible(saved.state)
            assert not saved.state[f"{SRD}.state"]["pending_srd_action_stack"]
            assert hero["resources"][level] == 2
            assert hero["spell_materials"][0]["quantity"] == 2
            assert "Casting failed" in str(receipt.result["result_card"])
            assert "Slow gesture roll" in str(receipt.result["result_card"])
            assert not any(
                r.get("spell") == "stoneskin" for r in saved.state[f"{SRD}.state"]["turn_effects"]
            )
        else:
            assert not _invisible(saved.state)
            assert hero["resources"][level] == 3
            event_id = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
            decision = deepcopy(
                saved.state[f"{SRD}.state"]["pending_srd_actions"][event_id]["slow_cast"]
            )
            assert decision["failed"] is False
            assert decision["caster_state_id"] == "hero"
            assert (
                decision["roll"] is None
                if mode == "inactive"
                else decision["roll"]["results"] == [4]
            )
            assert "completion pending" in str(receipt.result["result_card"])
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved
        assert reopened.execute_action(command) == receipt
        if mode != "failure":
            finished, completion = _act(
                reopened, "complete_srd_pending_action", pending_action_id=event_id
            )
            assert finished.status is ActionStatus.COMMITTED, finished.result
            saved = reopened.snapshot("casting")
            assert saved.state[f"{SRD}.entities"]["hero"]["resources"][level] == 2
            assert not saved.state[f"{SRD}.state"]["pending_srd_action_stack"]
            assert "Casting failed" not in str(finished.result["result_card"])
            assert reopened.execute_action(completion) == finished
            assert reopened.snapshot("casting") == saved
            assert len(actual_owner) == (0 if mode == "inactive" else 1)
    with AuthorityOwner.open(database, "casting-owner") as again:
        assert again.snapshot("casting") == saved
