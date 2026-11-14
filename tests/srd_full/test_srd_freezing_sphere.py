"""One paid globe, its real clock and native Cold/ice consequences survive saved choices."""

import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_freezing_sphere as sphere
from scripts import srd_reaction_spells as reactions
from scripts import srd_spell_light_damage_riders as area_consequences
from scripts import srd_spell_object_damage as object_damage
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-freezing-sphere/ruleset.yaml")
)
PARENT_SHA = "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
SRD = cards.SRD


@lru_cache
def _runtime():
    assert sha256(PARENT.read_bytes()).hexdigest() == PARENT_SHA
    original = PARENT.read_text(encoding="utf-8")
    text = sphere.bind_runtime_consumers(
        area_consequences.bind_area_consequences(object_damage.bind_runtime_consumers(original))
    )
    assert sphere.bind_runtime_consumers(text) == text
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    parent = yaml.load(original, Loader=yaml.CSafeLoader)
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    changed = {}
    with patch.object(
        reactions,
        "ORDINARY_SUPPORT_TOOLS",
        sphere.PUBLIC_TOOLS - {"resolve_srd_freezing_sphere_globe"},
    ):
        reactions._ordinary_support_fences(runtime["tools"], changed)
    runtime["tools"].update(changed)
    return yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode(), runtime


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
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


class Rolls(Random):
    def __init__(self):
        super().__init__(0)
        self.sides = []

    def randint(self, low, high):
        self.sides.append(high)
        return 10 if high == 20 else 3


@pytest.fixture(autouse=True)
def native(monkeypatch):
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[1]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())


def _case():
    """Supply the living Wizard's native damage facts and actual untrained save modifiers."""
    state, data, params = cards._spell_case(sphere.SPELL)
    state[f"{SRD}.state"]["lasting_tick"] = 0
    state[f"{SRD}.entities"]["hero"].update(
        saving_throw_modifiers={
            ability: (data["abilities"][ability] - 10) // 2
            for ability in ("constitution", "dexterity")
        },
        death_successes=0,
        death_failures=0,
        stable=False,
        stable_recovery_hours_remaining=0,
    )
    for who in ("guard", "other"):
        actor = state[f"{SRD}.entities"][who]
        actor.update(hp=1000, max_hp=1000, temp_hp=0, skill_check_modifiers={"athletics": 30})
        actor["saving_throw_modifiers"]["constitution"] = -30 if who == "guard" else 30
    params.update(targets=[], freezing_sphere_hold=True)
    return state, data, params


def _cast(state, data, params):
    result = budget._run("cast_spell", state, participant_data=data, **params).result
    assert result["status"] == "committed", result
    return next(
        row
        for row in state[f"{SRD}.state"]["turn_effects"]
        if row.get("kind") == "freezing_sphere_globe"
    )


def test_paid_globe_transfer_throw_native_scaled_packet_and_water_escape(monkeypatch):
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    state, data, params = _case()
    params["slot_resource"] = "spell_slot_7"
    globe = deepcopy(_cast(state, data, params))
    assert rolls.sides == []
    assert globe["native"]["slot_level"] == 7
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is None
    source = globe["cast_id"]
    result = budget._run(
        "handle_srd_freezing_sphere_globe",
        state,
        actor_state="hero",
        source_id=source,
        operation="give",
        recipient_state="guard",
        distance=3,
    )
    assert result.result["status"] == "committed"
    result = budget._run(
        "handle_srd_freezing_sphere_globe",
        state,
        actor_state="guard",
        source_id=source,
        operation="throw",
        distance=40,
        location="Pond impact",
    )
    assert result.result["status"] == "committed"
    pending = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("complete_srd_pending_action", state, pending_action_id=pending)
    assert state == before
    result = budget._run(
        "resolve_srd_freezing_sphere_globe",
        state,
        pending_action_id=pending,
        targets=["guard", "other"],
        freezing_water="Pond surface",
        freezing_surface_swimmers=["guard", "other"],
    ).result
    assert result["status"] == "committed"
    assert rolls.sides == [6] * 11 + [20, 20]
    actors = state[f"{SRD}.entities"]
    assert actors["guard"]["hp"] == 967 and actors["other"]["hp"] == 984
    assert all("restrained" in actors[who]["conditions"] for who in ("guard", "other"))
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
    ice = state[f"{SRD}.state"]["freezing_sphere_ice"][source]
    assert ice["save_dc"] == globe["native"]["casting_save_dc"]
    state[f"{SRD}.combat"].update(active_actor="guard", action_available=True)
    escaped = budget._run(
        "escape_srd_freezing_sphere_ice", state, actor_state="guard", source_id=source
    ).result
    assert escaped["context"]["_outcome"] == "success"
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "restrained" in state[f"{SRD}.entities"]["other"]["conditions"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert "restrained" not in state[f"{SRD}.entities"]["other"]["conditions"]


def test_real_globe_clock_opens_one_fenced_expiry_choice_and_preserves_original_dc():
    state, data, params = _case()
    globe = deepcopy(_cast(state, data, params))
    budget._run(
        "handle_srd_freezing_sphere_globe",
        state,
        actor_state="hero",
        source_id=globe["cast_id"],
        operation="set_down",
        location="Stone shelf",
    )
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert not state[f"{SRD}.state"].get("pending_srd_action_stack")
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=2)
    lasting = state[f"{SRD}.state"]
    pending = lasting["pending_srd_action_stack"][-1]
    saved = lasting["pending_srd_actions"][pending]
    assert saved["context"]["reason"] == "expiry"
    assert saved["context"]["explosion_tick"] == 20
    assert saved["context"]["globe"]["location"] == "Stone shelf"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state == before
    state[f"{SRD}.entities"]["hero"]["is_alive"] = False
    result = budget._run(
        "resolve_srd_freezing_sphere_globe",
        state,
        pending_action_id=pending,
        targets=["guard"],
        freezing_water="Pond surface",
        freezing_surface_swimmers=["guard"],
    ).result
    assert result["status"] == "committed"
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 970
    assert state[f"{SRD}.state"]["pending_srd_actions"][pending]["status"] == "resolved"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    assert state[f"{SRD}.state"]["freezing_sphere_ice"][globe["cast_id"]]["expires_tick"] == 40
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["has_freezing_sphere_sources"] is False


@lru_cache
def _archive():
    encoded, runtime = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [r for r in record["tools"] if r["name"] in runtime["tools"]]
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]])
    for name in sorted(sphere.PUBLIC_TOOLS):
        record["tools"] = [r for r in record["tools"] if r["name"] != name]
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
        links["tools"] = [n for n in links.get("tools", []) if n in runtime["tools"]]
    content["inclusions"].append(
        {
            "id": "freezing-sphere-globe-choice",
            "kind": "ruleset_runtime",
            "family": "spells",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": sorted(sphere.PUBLIC_TOOLS),
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["freezing-sphere-paid-expiry-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda r: r["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_public_paid_globe_expiry_choice_reopens_and_resolves_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    database = tmp_path / "sphere.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, sphere.SPELL, target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        paid, paid_command = _act(
            owner,
            "cast_spell_outside_combat",
            **(params | {"targets": [], "freezing_sphere_hold": True}),
        )
        assert paid.status is ActionStatus.COMMITTED, paid.result
        assert rolls.sides == []
        advanced, advance_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=10)
        assert advanced.status is ActionStatus.COMMITTED, advanced.result
        saved = owner.snapshot("casting")
        pending = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert (
            saved.state[f"{SRD}.state"]["pending_srd_actions"][pending]["context"]["explosion_tick"]
            == 20
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(paid_command) == paid
        assert owner.execute_action(advance_command) == advanced
        finished, finish_command = _act(
            owner, "resolve_srd_freezing_sphere_globe", pending_action_id=pending, targets=["guard"]
        )
        assert finished.status is ActionStatus.COMMITTED, finished.result
        final = owner.snapshot("casting")
        assert rolls.sides == [6] * 10 + [20]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        assert not final.state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert owner.execute_action(finish_command) == finished
        assert owner.snapshot("casting") == final


def test_scene_globe_bounds_reject_each_axis_and_requalify_the_actual_holder():
    state, data, params = _case()
    _admit_scene(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    recipient = next(row for row in scene["entities"] if row["id"] == "guard")
    recipient["position"] = [3, 0, 0]
    points = [[-0.5, 0, 3], [0.5, 0, 3], [0, -0.5, 3], [0, 0.5, 3], [0, 0, -0.1], [0, 0, 6.1]]
    for point in points:
        budget._reject(
            "cast_spell", state, participant_data=data, **(params | {"freezing_globe_point": point})
        )
    assert dice._rng.sides == []
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 3
    globe = _cast(state, data, params | {"freezing_globe_point": [0, 0, 3]})
    source = globe["cast_id"]
    budget._reject(
        "handle_srd_freezing_sphere_globe",
        state,
        actor_state="hero",
        source_id=source,
        operation="throw",
        distance=0,
        point=[41, 0, 0],
        location="Outside actual throw range",
    )
    for point in [None, *[[p[0] + 3, p[1], p[2]] for p in points]]:
        budget._reject(
            "handle_srd_freezing_sphere_globe",
            state,
            actor_state="hero",
            source_id=source,
            operation="give",
            recipient_state="guard",
            distance=3,
            point=point,
        )
    budget._run(
        "handle_srd_freezing_sphere_globe",
        state,
        actor_state="hero",
        source_id=source,
        operation="give",
        recipient_state="guard",
        distance=3,
        point=[3, 0, 3],
    )
    assert dice._rng.sides == []
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    recipient = next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "guard"
    )
    recipient["height"] = 2
    budget._reject("advance_srd_lasting_time", state, elapsed_rounds=10)
    recipient["height"] = 6
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=10)
    lasting = state[f"{SRD}.state"]
    pending = lasting["pending_srd_action_stack"][-1]
    frame = lasting["pending_srd_actions"][pending]
    assert frame["context"]["reason"] == "expiry"
    assert frame["context"]["globe"]["origin"] == [3, 0, 3]
    assert frame["context"]["globe"]["holder"] == "guard"
    assert dice._rng.sides == []
    budget._run(
        "resolve_srd_freezing_sphere_globe",
        state,
        pending_action_id=pending,
        targets=["hero", "guard", "other"],
    )
    assert dice._rng.sides == [6] * 10 + [20, 20, 20]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 970
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    assert state[f"{SRD}.state"]["pending_srd_actions"][pending]["status"] == "resolved"
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
