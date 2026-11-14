"""Factual hand records qualify current body, inventory, held items and actual shield."""

import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_reaction_spells as reactions
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_scene_hand_occupancy import PUBLIC_TOOLS, extend_runtime

from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import SOURCE
from tests.srd_full.test_srd_spell_illumination import _case as _source_case


@lru_cache
def _runtime():
    encoded = (SOURCE / "runtime/ruleset.yaml").read_bytes()
    assert sha256(encoded).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    # The normal builder supplies LF text; preserve the exact native dictionary
    # while normalizing only the frozen artifact's Windows line endings.
    text = extend_runtime(encoded.decode().replace("\r\n", "\n"))
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    # Root owns this final rebind of the established ordinary support fence.
    changed = {}
    with patch.object(reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        reactions._ordinary_support_fences(runtime["tools"], changed)
    runtime["tools"].update(changed)
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


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
            input_schema=owned_payload(_tool_schema(runtime["tools"][name])),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _case():
    state, data, params = _source_case("produce_flame")
    actor = state[f"{SRD}.entities"]["hero"]
    actor["inventory"] += ["Greatsword", "Torch", "Shield"]
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["Greatsword"])
    return state, data, params


def _body(state):
    return next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hero")


def _record(state, holdings=None, capacity=2):
    return _run(
        "record_srd_scene_hand_occupancy",
        state,
        actor_state="hero",
        hand_capacity=capacity,
        holdings=(
            [{"inventory_item": "Greatsword", "hand_indices": [0]}]
            if holdings is None
            else holdings
        ),
    ).result["context"]


def _qualify(state):
    return _run("qualify_srd_scene_hand_occupancy", state, actor_state="hero").result["context"]


@pytest.mark.parametrize("grasp", [[0], [0, 1]])
def test_two_handed_attack_property_does_not_invent_mere_holding_occupancy(grasp):
    state, _, _ = _case()
    before = deepcopy(state[f"{SRD}.entities"]["hero"])
    original_body = deepcopy(_body(state))
    _record(state, [{"inventory_item": "Greatsword", "hand_indices": grasp}])
    context = _qualify(state)
    assert context["_scene_free_hand_count"] == 2 - len(grasp)
    assert state[f"{SRD}.entities"]["hero"] == before
    assert _body(state)["position"] == original_body["position"]
    assert _body(state)["metadata"] == original_body["metadata"] | {
        "srd_hand_occupancy": context["_scene_hand_record"]
    }


def test_actual_shared_grasp_and_multiple_hand_assignments_count_union():
    state, _, _ = _case()
    _run(
        "record_srd_control_held_items",
        state,
        target_state="hero",
        held_items=["Greatsword", "Torch"],
    )
    context = _record(
        state,
        [
            {"inventory_item": "Greatsword", "hand_indices": [0, 1]},
            {"inventory_item": "Torch", "hand_indices": [1]},
        ],
        capacity=3,
    )
    assert context["_scene_hands_used"] == [0, 1]
    assert _qualify(state)["_scene_free_hand_count"] == 1


def test_actual_shield_is_accounted_even_when_not_duplicated_in_held_list():
    state, _, _ = _case()
    state[f"{SRD}.entities"]["hero"]["worn_shield"] = {
        "inventory_item": "Shield",
        "armor_type": "shield",
        "magic_bonus": 0,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual wielded shield"):
        _record(state)
    assert state == before
    _record(
        state,
        [
            {"inventory_item": "Greatsword", "hand_indices": [0]},
            {"inventory_item": "Shield", "hand_indices": [1]},
        ],
    )
    assert _qualify(state)["_scene_free_hand_count"] == 0


@pytest.mark.parametrize("change", ["held", "inventory", "shield", "height", "footprint", "form"])
def test_current_equipment_and_body_changes_reject_stale_records_atomically(change):
    state, _, _ = _case()
    _record(state)
    actor = state[f"{SRD}.entities"]["hero"]
    if change == "held":
        _run("record_srd_control_held_items", state, target_state="hero", held_items=["Torch"])
    if change == "inventory":
        actor["inventory"].remove("Greatsword")
    if change == "shield":
        actor["worn_shield"] = {
            "inventory_item": "Shield",
            "armor_type": "shield",
            "magic_bonus": 0,
        }
    if change == "height":
        _body(state)["height"] += 1
    if change == "footprint":
        _body(state)["footprint"] = {"kind": "circle", "radius": 3}
    if change == "form":
        actor["form_current_source_id"] = "actual-admitted-changed-form"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _qualify(state)
    assert state == before


def test_real_relocation_preserves_body_bound_record_without_creating_equipment_state():
    state, _, _ = _case()
    _record(state)
    recorded = deepcopy(_body(state)["metadata"]["srd_hand_occupancy"])
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 5, 0])
    assert _qualify(state)["_scene_free_hand_count"] == 1
    assert _body(state)["metadata"]["srd_hand_occupancy"] == recorded


def test_empty_zero_hand_anatomy_and_missing_record_are_explicit():
    state, _, _ = _case()
    with pytest.raises(RulesetPreconditionRejected, match="Record current anatomical"):
        _qualify(state)
    _run("record_srd_control_held_items", state, target_state="hero", held_items=[])
    assert _record(state, [], capacity=0)["_scene_free_hand_count"] == 0


@pytest.mark.parametrize(
    "holdings",
    [
        [{"inventory_item": "Torch", "hand_indices": [0]}],
        [{"inventory_item": "Greatsword", "hand_indices": [2]}],
        [
            {"inventory_item": "Greatsword", "hand_indices": [0]},
            {"inventory_item": "Greatsword", "hand_indices": [1]},
        ],
    ],
)
def test_unheld_out_of_anatomy_and_duplicate_item_assignments_are_rejected(holdings):
    state, _, _ = _case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _record(state, holdings)
    assert state == before


@lru_cache
def _archive():
    runtime, encoded = _runtime()
    with patch.object(bundled_packages, "_bundled_srd_root", return_value=SOURCE):
        primary, reference = bundled_packages._srd_drafts()
    record = deepcopy(primary.record)
    for name in PUBLIC_TOOLS:
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
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": encoded}
    content = json.loads(payloads["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "scene-hand-anatomy-facts",
            "kind": "ruleset_runtime",
            "family": "equipment",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "tools": sorted(PUBLIC_TOOLS),
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["actual-hand-record-reopen-retry-pending-fence"]},
        }
    )
    content["inclusions"].sort(key=lambda r: r["id"])
    payloads["content/content-declarations.json"] = canonical_json_bytes(content)
    return bundled_packages._archive((NormalizedPackageDraft(record, payloads, 4), reference))


def test_public_record_reopens_retries_and_pending_cast_rejects_atomic_rewrite(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    database = tmp_path / "hand-facts.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "produce_flame")
        recorded, record_command = _act(
            owner,
            "record_srd_scene_hand_occupancy",
            actor_state="hero",
            hand_capacity=2,
            holdings=[{"inventory_item": "Greatsword", "hand_indices": [0]}],
        )
        assert recorded.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert owner.execute_action(record_command) == recorded
        begun, begin_command = _act(owner, "begin_srd_spell_action", **params)
        assert begun.status is ActionStatus.COMMITTED
        pending = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == pending
        assert owner.execute_action(record_command) == recorded
        assert owner.execute_action(begin_command) == begun
        with pytest.raises(MechanicsRejected, match="pending reaction window"):
            _act(
                owner,
                "record_srd_scene_hand_occupancy",
                actor_state="hero",
                hand_capacity=2,
                holdings=[{"inventory_item": "Greatsword", "hand_indices": [0, 1]}],
            )
        assert owner.snapshot("casting") == pending


def test_emission_and_public_fact_parameters_are_bounded():
    raw = (SOURCE / "runtime/ruleset.yaml").read_text()
    emitted = extend_runtime(raw)
    assert extend_runtime(emitted) == emitted
    runtime = yaml.load(emitted, Loader=yaml.CSafeLoader)
    properties = _tool_schema(runtime["tools"]["record_srd_scene_hand_occupancy"])["properties"]
    assert set(properties) == {"actor_state", "hand_capacity", "holdings", "lasting_state"}
    assert properties["hand_capacity"]["maximum"] == 8
    assert properties["holdings"]["maxItems"] == 9


def test_current_scalar_qualification_keeps_the_real_operation_cap(monkeypatch):
    state, _, _ = _case()
    _record(state)
    budgets = []
    original = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        original(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    assert _qualify(state)["_scene_free_hand_count"] == 1
    assert budgets and all(b.max_operations == 10000 for b in budgets)
    assert max(b.operations for b in budgets) < 10000
    print("Actual scalar hand qualification operations:", max(b.operations for b in budgets))
