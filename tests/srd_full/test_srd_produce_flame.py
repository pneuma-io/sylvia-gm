"""The retained flame crosses real action, scene, dice, damage and durable package seams."""

import importlib.util
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime
from scripts.srd_full.srd_produce_flame import PUBLIC_TOOLS, extend_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_illumination import _authored_runtime, _case, _records
from tests.srd_full.test_srd_spell_wave3_information import _entity, _wall

ROOT = Path(__file__).resolve().parents[2]


def _frozen_module(name, path):
    """Read an immutable sibling owner's emitter without importing mutable sibling code."""
    spec = importlib.util.spec_from_file_location("scripts." + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache
def _runtime():
    runtime, payload = _authored_runtime()
    if "hurl_srd_produce_flame" in runtime["tools"]:
        return runtime, payload
    text = payload.decode()
    if "resolve_srd_spell_object_attack" not in runtime["tools"]:
        objects = _frozen_module(
            "_produce_frozen_objects",
            ROOT.parent / "verification/.scratch/object-target-freeze-v4/scripts/"
            "srd_spell_object_targets.py",
        )
        text = objects.extend_runtime(text)
    if "qualify_srd_scene_enemy" not in runtime["tools"]:
        summons = _frozen_module(
            "_produce_frozen_enemy",
            ROOT.parent / "summons/.scratch/force-spells-v1/scripts/srd_spell_summoning.py",
        )
        text = _replace_tools(text, {"qualify_srd_scene_enemy": summons._scene_enemy_tool()})
    text = refresh_exploration_runtime(extend_runtime(text))
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
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
def _actual_owners(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(0))


def _flame_case(spell="produce_flame"):
    state, data, params = _case(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [30, 0, 0]
    scene["entities"][2]["position"] = [25, 5, 0]
    scene["entities"][3]["position"] = [100, 0, 0]
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, armor_class=1, temp_hp=0)
    return state, data, params


def _summon(**changes):
    state, data, params = _flame_case()
    data.update(changes)
    if "level" in changes:
        data["classes"][0]["level"] = changes["level"]
    _run("cast_spell", state, participant_data=data, **params)
    return state, data


def _hurl(state, data, **choices):
    return _run(
        "hurl_srd_produce_flame",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        **choices,
    )


def test_public_intent_schema_contains_no_roll_damage_geometry_or_visibility_verdicts():
    runtime, _ = _runtime()
    schema = _tool_schema(
        runtime["tools"]["hurl_srd_produce_flame"], tool_name="hurl_srd_produce_flame"
    )
    assert {"hurl_srd_produce_flame"} == PUBLIC_TOOLS
    assert set(schema["properties"]) == {"caster", "caster_state", "target", "object_id"}
    for choices in ({}, {"target": "guard", "object_id": "orb"}):
        state, data = _summon()
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="exactly one"):
            _hurl(state, data, **choices)
        assert state == before


@pytest.mark.parametrize("level,dice_count", [(1, 1), (5, 2), (11, 3), (17, 4)])
def test_later_magic_action_uses_actual_source_level_and_keeps_flame(level, dice_count):
    state, data = _summon(level=level)
    record = deepcopy(_records(state, "produce_flame"))
    result = _hurl(state, data, target="guard").result
    context = result["context"]
    assert context["_produce_attack_kind"] == "hit"
    assert len(context["_produce_damage"]["results"]) == dice_count
    assert context["_produce_attack_total"] == (
        context["_produce_attack"]["total"] + context["_produce_flame_attack_bonus"]
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - context["_produce_damage_rolled"]
    assert _records(state, "produce_flame") == record
    rows = {r["label"]: r["value"] for r in result["result_card"]["summary"]}
    assert rows["Fire damage"] == context["_produce_damage_applied"]
    assert rows["Attack total"] == context["_produce_attack_total"]
    assert rows["HP after"] == state[f"{SRD}.entities"]["guard"]["hp"]


@pytest.mark.parametrize("mode", ["miss", "critical", "resisted", "immune", "temp"])
def test_creature_attack_and_typed_damage_are_real(mode):
    state, data = _summon()
    target = state[f"{SRD}.entities"]["guard"]
    if mode == "miss":
        target["armor_class"] = 99
    if mode == "critical":
        target["conditions"] = ["paralyzed"]
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [5, 0, 0]
    if mode == "resisted":
        target["damage_resistances"] = ["fire"]
    if mode == "immune":
        target["damage_immunities"] = ["fire"]
    if mode == "temp":
        target["temp_hp"] = 100
    result = _hurl(state, data, target="guard").result["context"]
    expected = result["_produce_damage_rolled"]
    if mode == "miss":
        assert result["_produce_attack_kind"] == "miss"
        assert result["_produce_damage"] is None
        expected = 0
    elif mode == "critical":
        assert result["_produce_attack_kind"] == "critical_hit"
        assert len(result["_produce_damage"]["results"]) == 2 * result["_produce_flame_dice_count"]
    elif mode == "resisted":
        expected //= 2
    elif mode == "immune":
        expected = 0
    assert result["_produce_damage_applied"] == expected
    if mode == "temp":
        assert target["hp"] == 1000
        assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 100 - expected
    else:
        assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - expected


def test_actual_action_budget_initial_bonus_and_later_magic_and_rejected_retry():
    state, data, params = _flame_case()
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True, turn_active=True, active_actor="hero", action_available=True
    )
    _run("cast_spell", state, participant_data=data, **params)
    assert combat["action_available"] is True
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    _hurl(state, data, target="guard")
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="action budget"):
        _hurl(state, data, target="guard")
    assert state == before
    state[f"{SRD}.combat"]["action_available"] = True
    state[f"{SRD}.combat"]["normal_action_spent"] = False
    _hurl(state, data, target="guard")
    assert len(_records(state, "produce_flame")) == 1


@pytest.mark.parametrize("boundary", ["range", "plane", "wall", "expired"])
def test_current_geometry_source_expiry_reject_before_committed_cost_or_damage(boundary):
    state, data = _summon()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if boundary == "range":
        scene["entities"][1]["position"] = [60.01, 0, 0]
    elif boundary == "plane":
        scene["entities"][1]["metadata"]["plane"] = "ethereal"
    elif boundary == "wall":
        scene["geometry"] = [_wall(1)]
    else:
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _hurl(state, data, target="guard")
    assert state == before


@pytest.mark.parametrize(
    "enemy,condition,invisible,expected",
    [
        (False, [], False, False),
        (True, [], False, True),
        (True, ["incapacitated"], False, False),
        (True, ["blinded"], False, False),
        (True, [], True, False),
    ],
)
def test_ranged_nearby_disadvantage_uses_saved_enemy_capability_and_actual_sight(
    enemy,
    condition,
    invisible,
    expected,
):
    state, data = _summon()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][3]["position"] = [0, 5, 0]
    scene["entities"][3]["metadata"]["enemy_of"] = ["hero"] if enemy else []
    state[f"{SRD}.entities"]["other"]["conditions"] = condition
    if invisible:
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["invisible"]
    result = _hurl(state, data, target="guard").result["context"]
    assert result["_produce_nearby_disadvantage"] is expected
    assert _records(state, "produce_flame")


def test_prepared_object_uses_source_damage_and_never_becomes_a_creature():
    state, data = _summon()
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="orb",
        material="wood",
        size="large",
        durability="resilient",
    )
    result = _hurl(state, data, object_id="orb").result["context"]
    profile = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["metadata"]["srd_spell_object"]
    assert result["_produce_armor_class"] == 15
    assert result["_produce_damage_applied"] > 0
    assert profile["hp"] == 27 - result["_produce_damage_applied"]
    assert "orb" not in state[f"{SRD}.entities"]
    assert _records(state, "produce_flame")


def _prepare_source_mage(state, spell):
    supplies, _, _ = _case(spell)
    materials = supplies[f"{SRD}.entities"]["hero"]["spell_materials"]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Mage",
        source="Synthetic creature spellcasting profile",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=2,
        size_rank=2,
        constitution_save=2,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        cantrip_scaling_level=11,
        spells=[{"spell": spell, "uses_per_day": 0 if spell == "produce_flame" else 1}],
        inventory=[item["inventory_item"] for item in materials],
        spell_materials=materials,
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [5, 5, 0], participant_ref="mage", creature_type="humanoid")
    )


def test_prepared_npc_retained_action_uses_its_saved_source_and_actual_roll():
    state, _, params = _flame_case()
    _prepare_source_mage(state, "produce_flame")
    params.update(caster=None, caster_state="mage", slot_resource=None)
    _run("cast_spell", state, **params)
    result = _run("hurl_srd_produce_flame", state, caster_state="mage", target="guard").result
    context = result["context"]
    assert context["_produce_flame_attack_bonus"] == 7
    assert context["_produce_flame_dice_count"] == 3
    assert context["_produce_attack_total"] == context["_produce_attack"]["total"] + 7
    assert len(context["_produce_damage"]["results"]) == 3
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["produce_flame"] == 0


@pytest.mark.parametrize("blinded", [False, True])
def test_faerie_fire_object_advantage_requires_actual_visibility(blinded):
    state, data = _summon()
    _prepare_source_mage(state, "faerie_fire")
    _, _, params = _case("faerie_fire")
    params.update(
        caster=None, caster_state="mage", slot_resource=None, illumination_position=[20, 0, 0]
    )
    _run("cast_spell", state, **params)
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="orb",
        material="wood",
        size="large",
        durability="resilient",
    )
    if blinded:
        state[f"{SRD}.entities"]["hero"]["conditions"] = ["blinded"]
    result = _hurl(state, data, object_id="orb").result["context"]
    assert result["_produce_can_see"] is not blinded
    assert result["_produce_advantage"] is not blinded
    assert result["_produce_disadvantage"] is blinded


def test_partial_cover_comes_from_actual_scene_exposure():
    state, data = _summon()
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [_wall(1, high_y=0)]
    result = _hurl(state, data, target="guard").result["context"]
    assert result["_produce_armor_class"] == 3
    assert result["_produce_armor_class"] > state[f"{SRD}.entities"]["guard"]["armor_class"]


@pytest.mark.parametrize("spell,ends", [("invisibility", True), ("greater_invisibility", False)])
def test_real_retained_attack_roll_ends_only_ordinary_invisibility(spell, ends):
    state, data = _summon()
    _prepare_source_mage(state, spell)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][-1]["position"] = [5, 0, 0]
    _, _, params = _case(spell)
    params.update(caster=None, caster_state="mage", slot_resource=None, target="hero")
    _run("cast_spell", state, **params)
    assert "invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]
    state[f"{SRD}.entities"]["guard"]["armor_class"] = 99
    result = _hurl(state, data, target="guard").result["context"]
    assert result["_produce_attack_kind"] == "miss"
    assert ("invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]) is not ends
    assert bool(_records(state, spell)) is not ends
    assert _records(state, "produce_flame")


@lru_cache
def _test_archive():
    """Admit the real authored consumers in a test closure without a canonical build."""
    runtime, payload = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for tool in record["tools"]:
        if tool["name"] in runtime["tools"]:
            tool["input_schema"] = _tool_schema(
                runtime["tools"][tool["name"]], tool_name=tool["name"]
            )
    added = ["hurl_srd_produce_flame", "prepare_srd_spell_object"]
    for name in added:
        if any(tool["name"] == name for tool in record["tools"]):
            continue
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.spells",
                "input_schema": _tool_schema(runtime["tools"][name], tool_name=name),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
            }
        )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": payload}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "produce-flame-retained-action",
            "kind": "ruleset_runtime",
            "family": "illumination_spell",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": added,
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["produce-flame-public-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda item: item["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payloads, primary.format_version), reference))


def test_public_cast_later_creature_and_object_actions_reopen_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _flame_case)
    database = tmp_path / "flame.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "produce_flame", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        receipt, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED
        source = deepcopy(_records(owner.snapshot("casting").state, "produce_flame"))
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "hurl_srd_produce_flame",
                caster="hero",
                caster_state="hero",
                target="guard",
                hit=True,
                damage=999,
            )
        first, first_command = _act(
            owner, "hurl_srd_produce_flame", caster="hero", caster_state="hero", target="guard"
        )
        assert first.status is ActionStatus.COMMITTED, first.result
        assert (
            first.result["result_card"]["equation"]["total"]
            == (first.result["context"]["_produce_attack_total"])
        )
        prepared, _ = _act(
            owner,
            "prepare_srd_spell_object",
            object_id="orb",
            material="wood",
            size="large",
            durability="resilient",
        )
        assert prepared.status is ActionStatus.COMMITTED
        second, second_command = _act(
            owner, "hurl_srd_produce_flame", caster="hero", caster_state="hero", object_id="orb"
        )
        assert second.status is ActionStatus.COMMITTED, second.result
        saved = owner.snapshot("casting")
        assert _records(saved.state, "produce_flame") == source
        assert second.result["context"]["_produce_damage_applied"] > 0
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("A durable Produce Flame retry rolled dice again")

        monkeypatch.setattr(dice, "_rng", NoReroll(99))
        assert reopened.execute_action(first_command) == first
        assert reopened.execute_action(second_command) == second
        assert reopened.snapshot("casting") == saved
        monkeypatch.setattr(dice, "_rng", Random(0))
        third, _ = _act(
            reopened, "hurl_srd_produce_flame", caster="hero", caster_state="hero", target="guard"
        )
        assert third.status is ActionStatus.COMMITTED
        assert _records(reopened.snapshot("casting").state, "produce_flame") == source
