"""One paid object Mishap retains real damage, held identity and arrival across retries."""

import ast
import importlib.util
import json
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts import srd_spell_object_damage as damage
from scripts import srd_spell_travel as travel
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_control_behaviors import _when as branch
from scripts.srd_full.srd_reaction_spells import _compose_teleport_resolution
from scripts.srd_full.srd_spell_object_utilities import _fact_tools
from scripts.srd_full.srd_spell_supplies import _effect_lists
from scripts.srd_full.srd_spell_travel_teleport_objects import OBJECT_CAST_CHOICES
from scripts.srd_full.srd_timed_actor_defenses import _call, _load_runtime, _require, _set, _tool
from scripts.srd_full.srd_timed_senses import _push

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests.srd_full.test_srd_spell_travel import SRD, _case, _point, _TravelDice, dice

BASE = Path(".scratch/celestial-initial-sight-frozen-v1/excluded/runtime.yaml")
OBJECT_BASE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-spell-travel-teleport-objects/ruleset.yaml")
)


def _calls(effects):
    for e in effects:
        if "call" in e:
            yield e
        for key in ("on_true", "on_false"):
            yield from _calls(e.get(key, []))
        yield from _calls(e.get("for_each", {}).get("do", []))


@lru_cache
def _runtime():
    base = BASE.read_bytes()
    assert sha256(base).hexdigest() == (
        "65498ad3fe2bdf8b41bd30b4e25ba703c181fe5498b32c460bd5377688fe1045"
    )
    raw = OBJECT_BASE.read_bytes()
    assert sha256(raw).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    r = _load_runtime(base)
    object_tools = _load_runtime(raw)["tools"]
    pending = ["resolve_srd_spell_object_attack", "resolve_srd_spell_object_relation"]
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        tool = object_tools[name]
        if name not in r["tools"]:
            r["tools"][name] = deepcopy(tool)
        pending.extend(c["call"] for c in _calls(tool["effects"]))
    tools = r["tools"]
    tools["record_srd_object_holder"] = _fact_tools()["record_srd_object_holder"]
    tools[damage.QUALIFY] = damage._qualification(tools)
    tools[damage.PACKET] = damage._packet(tools)
    tools[damage.TELEPORT_PACKET] = damage._packet(tools, teleport=True)
    tools.update(damage._preparation_facts(tools))
    generated = travel._teleport_tools(tools)
    travel._normalize(generated)
    travel._wrap(generated)
    tools.update(generated)
    changed = {"complete_srd_pending_action": deepcopy(tools["complete_srd_pending_action"])}
    _compose_teleport_resolution(tools, changed)
    for tool in changed.values():
        normalized = _tool("", tool["parameters"], tool["effects"])
        tool["effects"] = normalized["effects"]
        for parameter in tool["parameters"]:
            if parameter["name"] == "lasting_state":
                parameter["model_hidden"] = True
    tools.update(changed)
    # Forward only the new source choices through existing actual public cast delegates.
    names = {"cast_spell", "cast_spell_outside_combat", "begin_srd_spell_action"}
    for name in names:
        tool = tools[name]
        known = {p["name"] for p in tool["parameters"]}
        tool["parameters"].extend(
            deepcopy([p for p in OBJECT_CAST_CHOICES if p["name"] not in known])
        )
        for call in _calls(tool["effects"]):
            if call["call"] in names | {"validate_srd_teleport_cast"}:
                call["params"].update(
                    {p["name"]: "{" + p["name"] + "}" for p in OBJECT_CAST_CHOICES}
                )
    encoded = yaml.dump(r, Dumper=yaml.CSafeDumper, sort_keys=False).encode()
    return r, encoded


@pytest.fixture
def object_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    drafts = bundled_packages._srd_drafts

    def declared():
        primary, reference = drafts()
        record = deepcopy(primary.record)
        payload = dict(primary.payloads)
        name = "record_srd_object_holder"
        if name not in {row["name"] for row in record["tools"]}:
            record["tools"].append(
                {
                    "name": name,
                    "capability": "dnd.srd.5.2.1.spells",
                    "input_schema": _tool_schema(_runtime()[0]["tools"][name], tool_name=name),
                    "writers": ["dm"],
                    "turn_statuses": ["open"],
                    "state_predicates": [],
                }
            )
            declarations = json.loads(payload["content/content-declarations.json"])
            for inclusion in declarations["inclusions"]:
                if "cast_spell" in inclusion.get("links", {}).get("tools", []):
                    inclusion["links"]["tools"] = sorted(set(inclusion["links"]["tools"]) | {name})
            payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", declared)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _object(state):
    return next(
        b for b in state[f"{SRD}.state"]["spatial_scene"]["entities"] if b["id"] == "parcel"
    )


def _case_for_cast(_spell):
    state, data, params = _case(
        "teleport",
        travel_destination_subject="orb",
        teleport_object_subject="parcel",
        teleport_holder_willing=True,
    )
    params.pop("targets", None)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    anchor = next(b for b in scene["entities"] if b["id"] == "orb")
    anchor.update(position=[100, 0, 0], movement="passable", visibility="clear")
    parcel = deepcopy(anchor)
    parcel.update(id="parcel", position=[2, 0, 0], height=1)
    parcel["metadata"] = {"is_object": True, "plane": "material", "lighting": "bright"}
    scene["entities"].append(parcel)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"].extend(["parcel-item", "untouched-item"])
    hero["held_items"] = ["parcel-item", "untouched-item"]
    return state, data, params


def test_default_area_packet_and_physical_release_owner_remain_unchanged():
    parent = Path(".scratch/teleport-object-parent-v1/srd_spell_object_damage.py")
    assert sha256(parent.read_bytes()).hexdigest() == (
        "30bfc6a884f2ba6c60eda2c0c439ff6da2992960655c11bd13616d3a08018cef"
    )
    spec = importlib.util.spec_from_file_location("scripts._object_damage_parent", parent)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    tools = _runtime()[0]["tools"]
    assert damage._packet(tools) == original._packet(tools)
    assert damage._qualification(tools) == original._qualification(tools)
    assert damage._preparation_facts(tools) == original._preparation_facts(tools)
    released = tools["release_srd_teleport_carried_object"]
    original_release = tools["drop_srd_heated_object"]
    assert released["effects"][3:] == original_release["effects"]


def test_object_policy_keeps_final_ice_and_jar_suffix():
    ice_path = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-spell-travel-teleport-objects/srd_environment_wall_ice.py")
    )
    jar_path = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-spell-travel-teleport-objects/srd_magic_jar_container.py")
    )
    assert sha256(ice_path.read_bytes()).hexdigest() == (
        "0b92572e7e7a42efdc1a6564394ed096cd9931e8f09143268bbea226f1a62f77"
    )
    assert sha256(jar_path.read_bytes()).hexdigest() == (
        "0d6ce0938999e9a1c61df8d0f69ea27d8e845a243eae20896f8cbfaedfbc644b"
    )
    binder = next(
        n
        for n in ast.parse(ice_path.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == "bind_objects"
    )
    namespace = {
        "deepcopy": deepcopy,
        "_effect_lists": _effect_lists,
        "_call": _call,
        "_set": _set,
        "_require": _require,
        "_when": branch,
        "_push": _push,
    }
    exec(compile(ast.Module(body=[binder], type_ignores=[]), str(ice_path), "exec"), namespace)
    tools = deepcopy(_runtime()[0]["tools"])
    tools.update(namespace["bind_objects"](tools))
    literals = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in ast.parse(jar_path.read_text()).body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id in {"HOOK_CONDITION", "CALLBACK"}
    }
    owner = tools["resolve_srd_spell_object_attack"]
    owner["effects"].append(
        branch(
            literals["HOOK_CONDITION"],
            [
                _call(literals["CALLBACK"], {"object_id": "{object_id}"}),
            ],
        )
    )
    hit_index = next(
        i
        for i, e in enumerate(owner["effects"])
        if e.get("condition")
        == "_object_attack_kind == 'hit' or _object_attack_kind == 'critical_hit'"
    )
    tail = owner["effects"][hit_index + 1 :]
    normalized = damage._tool_scene("", [], deepcopy(tail))["effects"]
    packet = damage._packet(tools, teleport=True)
    assert packet["effects"][-len(tail) :] == normalized
    assert "settle_srd_wall_of_ice_panel" in str(tail)
    assert literals["CALLBACK"] in str(packet)


def test_paid_object_teleport_mishap_and_willing_release_reopen_retry(
    object_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _case_for_cast)
    game = _TravelDice([(100, 1), (10, 2), (10, 3), (10, 4), (100, 100)])
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    db = tmp_path / "teleport-object.db"
    with AuthorityOwner.open(db, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "teleport")
        mechanics = StatelessMcpMechanics(owner)
        base = ActionCommand(
            campaign_id="casting",
            action_id="facts",
            idempotency_key="facts",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_object_utility_facts",
            parameters={},
        )

        def act(identity, tool, values):
            cmd = replace(
                base,
                action_id=identity,
                idempotency_key=identity,
                tool_name=tool,
                expected_revision=owner.snapshot("casting").revision,
                parameters=values,
            )
            receipt = mechanics.execute_action(cmd)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            saved = owner.snapshot("casting")
            assert mechanics.execute_action(cmd) == receipt
            assert owner.snapshot("casting") == saved
            return cmd, saved

        act(
            "classify",
            "record_srd_object_utility_facts",
            {
                "object_id": "parcel",
                "weight_pounds": 1,
                "manufactured": True,
                "metal": False,
                "object_kind": "object",
                "inventory_item": "parcel-item",
            },
        )
        act("size", "record_srd_telekinesis_object_size", {"object_id": "parcel", "size": "large"})
        act(
            "prepare",
            "prepare_srd_spell_object",
            {
                "object_id": "parcel",
                "material": "iron",
                "size": "large",
                "durability": "resilient",
                "magic_status": "magical",
            },
        )
        act(
            "hold",
            "record_srd_object_holder",
            {
                "object_id": "parcel",
                "actor_state": "hero",
                "held_as": "carried",
            },
        )
        act(
            "knowledge",
            "record_srd_teleport_destination",
            {
                "caster_state": "hero",
                "destination_subject": "orb",
                "familiarity": "described",
            },
        )
        before = owner.snapshot("casting")
        wrong = replace(
            base,
            action_id="unwilling",
            idempotency_key="unwilling",
            expected_revision=before.revision,
            tool_name="cast_spell_outside_combat",
            parameters=dict(params, teleport_holder_willing=False),
        )
        with pytest.raises(MechanicsRejected, match="must be willing"):
            mechanics.execute_action(wrong)
        assert owner.snapshot("casting").state == before.state and not game.consumed
        cast, saved = act("paid", "cast_spell_outside_combat", params)
        hero = saved.state[f"{SRD}.entities"]["hero"]
        record = hero["teleport_resolution"]
        assert record["mode"] == "object" and record["stage"] == "mishap"
        assert [m["target"] for m in record["party"]] == ["parcel"]
        assert hero["resources"]["spell_slot_7"] == 2
        assert record["reaction_action_id"] is not None
        continuation = replace(
            cast,
            tool_name="continue_srd_teleport_resolution",
            parameters={"caster_state": "hero", "resolution_id": record["id"]},
        )
    with AuthorityOwner.open(db, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        roll = replace(
            continuation,
            action_id="mishap-roll",
            idempotency_key="mishap-roll",
            expected_revision=saved.revision,
        )
        receipt = mechanics.execute_action(roll)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert _object(saved.state)["metadata"]["srd_spell_object"]["hp"] == 27
        assert saved.state[f"{SRD}.entities"]["hero"]["teleport_resolution"]["pending_mishap"][
            "roll"
        ]["results"] == [2, 3, 4]
        assert mechanics.execute_action(roll) == receipt and owner.snapshot("casting") == saved
    with AuthorityOwner.open(db, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        for stage in ("damage", "percentile", "arrival"):
            cmd = replace(
                continuation,
                action_id=stage,
                idempotency_key=stage,
                expected_revision=owner.snapshot("casting").revision,
            )
            receipt = mechanics.execute_action(cmd)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            saved = owner.snapshot("casting")
            assert mechanics.execute_action(cmd) == receipt and owner.snapshot("casting") == saved
        hero = saved.state[f"{SRD}.entities"]["hero"]
        obj = _object(saved.state)
        assert obj["metadata"]["srd_spell_object"]["hp"] == 18
        assert obj["metadata"]["carried_by"] is None and obj["metadata"]["worn_by"] is None
        assert obj["position"] == [100, 0, 0] and _point(saved.state) == [0, 0, 0]
        assert hero["hp"] == 500 and hero["resources"]["spell_slot_7"] == 2
        assert "parcel-item" not in hero["inventory"] and hero["held_items"] == ["untouched-item"]
        assert hero["dropped_items"] == ["parcel-item"]
        assert hero["teleport_resolution"]["status"] == "completed"
        assert saved.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert game.rolls == []
