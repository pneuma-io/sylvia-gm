"""A real held globe resolves before its completed damage opens earned Hellish Rebuke."""

import hashlib
import importlib.util
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_hellish_rebuke as hellish
from scripts import srd_received_damage_reactions as receiving
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime._expression_parser import MAX_EXPRESSION_LENGTH, parse_expression
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_freezing_sphere as base
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

SRD = base.SRD
RECEIVER = "globe_guard"
PUBLIC = base.sphere.PUBLIC_TOOLS | hellish.PUBLIC_TOOLS


DEPENDENCY = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-freezing-sphere-hellish-outer/input-1")
)
MANIFEST_SHA = "742fcb56085847284ebe5aef001849499e341b045ff7c985eab0e5435dbab942"


def _collapse_guards(value):
    """Compare complete tool bodies after rejoining adjacent pure guards with one error."""
    if isinstance(value, dict):
        return {key: _collapse_guards(child) for key, child in value.items()}
    if not isinstance(value, list):
        return value
    result = []
    for child in value:
        child = _collapse_guards(child)
        if (
            result
            and isinstance(child, dict)
            and set(child) == {"require"}
            and isinstance(result[-1], dict)
            and set(result[-1]) == {"require"}
            and child["require"]["error"] == result[-1]["require"]["error"]
        ):
            result[-1]["require"]["condition"] += " and " + child["require"]["condition"]
        else:
            result.append(child)
    return result


def _conditions(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "condition":
                yield child
            yield from _conditions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _conditions(child)


def test_split_guards_preserve_whole_tools_and_engine_expression_limit():
    assert hashlib.sha256((DEPENDENCY / "manifest.json").read_bytes()).hexdigest() == MANIFEST_SHA
    manifest = json.loads((DEPENDENCY / "manifest.json").read_text())
    path = DEPENDENCY / "scripts/srd_freezing_sphere.py"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == manifest["files"]["scripts/srd_freezing_sphere.py"]
    )
    spec = importlib.util.spec_from_file_location("scripts._freezing_original_guards", path)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    before = [original._retain({"parameters": []}), original._explode(), original._handle()]
    after = [base.sphere._retain({"parameters": []}), base.sphere._explode(), base.sphere._handle()]
    assert sorted(len(c) for c in _conditions(before) if len(c) > 500) == [558, 559, 583]
    assert MAX_EXPRESSION_LENGTH == 500
    repaired_distance = 0
    for group in original._lists(before[-1]["effects"]):
        for index, effect in enumerate(group):
            if effect.get("call") == "measure_srd_information_distance":
                assert effect["export_context"] == {"_info_distance": "_info_distance"}
                effect["export_context"] = {"_scene_distance": "_scene_distance"}
                assert group[index + 1] == {
                    "set": {"field": "_freezing_distance", "value": "_info_distance"}
                }
                group[index + 1]["set"]["value"] = "_scene_distance"
                repaired_distance += 1
    assert repaired_distance == 1
    assert _collapse_guards(after) == _collapse_guards(before)
    for condition in _conditions(after):
        assert len(condition) <= MAX_EXPRESSION_LENGTH, condition
        parse_expression(condition)


@lru_cache
def _runtime():
    _, native = base._runtime()
    parent = deepcopy(native)
    parent["definitions"]["spells"]["hellish_rebuke"] = deepcopy(hellish.PROFILE)
    for tool in parent["tools"].values():
        for parameter in tool["parameters"]:
            values = parameter.get("enum_values", [])
            if (
                parameter["name"] == "spell"
                and "shield" in values
                and "hellish_rebuke" not in values
            ):
                values.append("hellish_rebuke")
    queued = yaml.load(
        receiving.bind_receiving_events(
            yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
        ),
        Loader=yaml.CSafeLoader,
    )
    encoded = hellish.bind_runtime_consumers(
        yaml.dump(queued, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    assert yaml.load(hellish.bind_runtime_consumers(encoded), Loader=yaml.CSafeLoader) == runtime
    globe = "resolve_srd_freezing_sphere_globe"
    assert runtime["tools"][globe]["effects"][:-1] == queued["tools"][globe]["effects"]
    assert hellish.OPEN in str(runtime["tools"][globe]["effects"][-1])
    for name in (
        base.sphere.NATIVE_AREA,
        base.sphere.EXPLODE,
        base.sphere.WATER,
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_damage",
        "advance_srd_lasting_time",
    ):
        assert runtime["tools"][name] == queued["tools"][name]
        assert hellish.OPEN not in str(runtime["tools"][name]["effects"])
    assert runtime["tools"]["d20_test"] == native["tools"]["d20_test"]
    return encoded.encode(), runtime


@lru_cache
def _archive():
    encoded, runtime = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=base.SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [r for r in record["tools"] if r["name"] in runtime["tools"]]
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(runtime["tools"][row["name"]])
    for name in sorted(PUBLIC):
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
            "id": "freezing-sphere-earned-rebuke",
            "kind": "ruleset_runtime",
            "family": "spells",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": sorted(PUBLIC),
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["paid-globe-throw-cleanup-native-damage-rebuke-reopen-retry"]},
        }
    )
    content["inclusions"].sort(key=lambda r: r["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def _case(_spell):
    state, data, params = base._case()
    _admit_scene(state)
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        row["metadata"].update(is_object=False, creature_type="humanoid")
        if row["id"] == "guard":
            row["id"] = RECEIVER
            row["metadata"]["participant_ref"] = RECEIVER
            row["position"] = [40, 0, 0]
        elif row["id"] == "other":
            row["position"] = [150, 100, 0]
    params["freezing_globe_point"] = [0, 0, 3]
    return state, data, params


def _npc():
    return {
        "creature_id": RECEIVER,
        "name": "Globe receiving caster",
        "source": "Synthetic admitted humanoid with native saves and two Hellish Rebuke uses.",
        "armor_class": 12,
        "hit_points": 1000,
        "speed": 30,
        "size_rank": 2,
        "initiative_modifier": 0,
        "creature_type": "humanoid",
        "constitution_save": -5,
        "spellcasting_ability": "intelligence",
        "spellcasting_modifier": 4,
        "spell_attack_bonus": 7,
        "spell_save_dc": 15,
        "spells": [{"spell": "hellish_rebuke", "uses_per_day": 2}],
    }


@pytest.fixture
def _owners(monkeypatch):
    runtime = _runtime()[1]
    monkeypatch.setattr(base.cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(base.public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(base.public_casting, "_spell_case", _case)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.failure_kind, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def test_paid_globe_cleanup_before_real_rebuke_reopens_and_retries_once(
    tmp_path, monkeypatch, _owners
):
    rolls, budgets = base.Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    consume = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "sphere-hellish.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = base.public_casting._create_spell_campaign(
            owner,
            base.sphere.SPELL,
            entity_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0},
        )
        receipts.append(_act(owner, "prepare_srd_creature", **_npc()))
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        assert receipts[-1][0].status is ActionStatus.COMMITTED, receipts[-1][0].result
        state = owner.snapshot("casting").state
        globe = next(
            row
            for row in state[f"{SRD}.state"]["turn_effects"]
            if row.get("kind") == "freezing_sphere_globe"
        )
        source = globe["cast_id"]
        assert rolls.sides == []
        assert not state[f"{SRD}.state"].get("pending_srd_received_damage_ids")
        assert not state[f"{SRD}.state"].get("pending_srd_action_stack")
        assert state[f"{SRD}.entities"][RECEIVER]["reaction_available"] is True
        assert state[f"{SRD}.entities"][RECEIVER]["spell_uses_remaining"]["hellish_rebuke"] == 2
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        receipts.append(
            _act(
                owner,
                "start_combat_encounter_entities",
                first_actor="hero",
                second_actor=RECEIVER,
                first_surprised=False,
                second_surprised=False,
                tie_winner="first",
            )
        )
        receipts.append(_act(owner, "begin_combat_turn"))
        faces = deepcopy(rolls.sides)
        receipts.append(
            _act(
                owner,
                "handle_srd_freezing_sphere_globe",
                actor_state="hero",
                source_id=source,
                operation="throw",
                point=[40, 0, 0],
                location="Actual impact at receiver",
                distance=0,
            )
        )
        assert receipts[-1][0].status is ActionStatus.COMMITTED, receipts[-1][0].result
        assert receipts[-1][0].result["context"]["_freezing_distance"] == 40
        state = owner.snapshot("casting").state
        pending = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        row = state[f"{SRD}.state"]["pending_srd_actions"][pending]
        assert row["stage"] == "freezing_sphere_area"
        assert not state[f"{SRD}.state"].get("pending_srd_received_damage_ids")
        assert rolls.sides == faces
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(
            _act(
                owner,
                "resolve_srd_freezing_sphere_globe",
                pending_action_id=pending,
                targets=["hero", RECEIVER],
            )
        )
        state = owner.snapshot("casting").state
        lasting = state[f"{SRD}.state"]
        assert lasting["pending_srd_actions"][pending]["status"] == "resolved"
        assert not any(
            effect.get("kind") == "freezing_sphere_globe" and effect["cast_id"] == source
            for effect in lasting["turn_effects"]
        )
        assert lasting["pending_srd_action_stack"] != [pending]
        assert lasting["pending_srd_action_stack"], {
            "settled_globe_status": lasting["pending_srd_actions"][pending]["status"],
            "receiving_queue": lasting["pending_srd_received_damage_ids"],
            "receiving_statuses": {
                key: lasting["pending_srd_actions"][key]["status"]
                for key in lasting["pending_srd_received_damage_ids"]
            },
        }
        event = lasting["pending_srd_action_stack"][-1]
        row = lasting["pending_srd_actions"][event]
        assert row["kind"] == "received_damage" and row["status"] == "open"
        assert row["parent_action_id"] == pending and row["turn_actor"] == "hero"
        assert row["actor_state_id"] == "hero" and row["target_state_id"] == RECEIVER
        assert row["received_damage"]["effective_damage"] == 30
        assert row["received_damage"]["hp_before"] == 1000
        assert row["received_damage"]["hp_after"] == 970
        assert state[f"{SRD}.entities"][RECEIVER]["hp"] == 970
        assert state[f"{SRD}.entities"][RECEIVER]["reaction_available"] is True
        assert state[f"{SRD}.entities"][RECEIVER]["spell_uses_remaining"]["hellish_rebuke"] == 2
        assert state[f"{SRD}.entities"]["hero"]["hp"] == 970
        assert rolls.sides[len(faces) :] == [6] * 10 + [20, 20]
        faces = deepcopy(rolls.sides)
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(
            _act(
                owner,
                "begin_srd_hellish_rebuke",
                caster_state=RECEIVER,
                reaction_trigger_id=event,
                verbal_component_available=True,
                somatic_component_available=True,
            )
        )
        child = receipts[-1][0].result["context"]["_pending_action_id"]
        receipts.append(_act(owner, "complete_srd_pending_action", pending_action_id=child))
        assert "Hellish Rebuke" in str(receipts[-1][0].result["result_card"])
        receipts.append(_act(owner, "complete_srd_pending_action", pending_action_id=event))
        saved = owner.snapshot("casting")
        state, lasting = saved.state, saved.state[f"{SRD}.state"]
        assert not lasting["pending_srd_action_stack"]
        assert not lasting["pending_srd_received_damage_ids"]
        assert state[f"{SRD}.entities"][RECEIVER]["spell_uses_remaining"]["hellish_rebuke"] == 1
        assert state[f"{SRD}.entities"][RECEIVER]["reaction_available"] is False
        assert state[f"{SRD}.entities"]["hero"]["hp"] == 964
        assert rolls.sides[len(faces) :] == [10, 10, 20]
        assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED, result.result
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(b.max_operations == 10000 for b in budgets)
    print("Globe/Hellish max operations:", max(b.operations for b in budgets))
