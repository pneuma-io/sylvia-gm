"""Paid Telekinesis and actual ward access preserve saved facts and pending permissions."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import ToolDeclaration, canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_feather_fall_public as exact_payloads
from tests import test_srd_guards_wards_doors as doors
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_fine as fine
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity

SRD = cards.SRD
SCOPE = Path(os.environ["FC14_FINE_SCOPE"]) if "FC14_FINE_SCOPE" in os.environ else None
PACKAGE = Path(
    os.environ.get(
        "FC14_FINE_PACKAGE", str(Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1")
    )
)
ENCODED = ((SCOPE / "runtime.yaml") if SCOPE else PACKAGE / "runtime/ruleset.yaml").read_bytes()
RUNTIME = _load_runtime(ENCODED)
EXTRA_PUBLIC = {"record_srd_scene_hand_occupancy", "operate_srd_telekinesis_object"}


@lru_cache
def _mechanic(name):
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=ENCODED,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(RUNTIME["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@lru_cache
def _archive():
    if SCOPE is None:
        return bundled_packages.bundled_srd_archive()
    primary, reference = exact_payloads._linked_drafts()
    record = deepcopy(primary.record)
    record["tools"] = [row for row in record["tools"] if row["name"] in RUNTIME["tools"]]
    declared = {row["name"] for row in record["tools"]}
    for name in sorted(EXTRA_PUBLIC - declared):
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.spells",
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
                "input_schema": _tool_schema(RUNTIME["tools"][name], tool_name=name),
            }
        )
    for row in record["tools"]:
        row["input_schema"] = _tool_schema(RUNTIME["tools"][row["name"]], tool_name=row["name"])
    record["ui_surfaces"] = []
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": ENCODED}
    declarations = json.loads(payload["content/content-declarations.json"])
    names = {row["name"] for row in record["tools"]}
    for inclusion in declarations["inclusions"]:
        links = inclusion.get("links", {})
        if "tools" in links:
            links["tools"] = [n for n in links["tools"] if n in names]
            if "cast_spell_outside_combat" in links["tools"]:
                links["tools"] = sorted(set(links["tools"]) | EXTRA_PUBLIC)
        if "ui_surfaces" in links:
            links["ui_surfaces"] = []
    payload["content/content-declarations.json"] = canonical_json_bytes(declarations)
    result = bundled_packages._archive(
        (NormalizedPackageDraft(record, payload, primary.format_version), reference)
    )
    (SCOPE / "selected-public-package.zip").write_bytes(result)
    return result


@pytest.fixture(autouse=True)
def actual_owners(monkeypatch):
    monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: PACKAGE)
    monkeypatch.setattr(public, "ROOT", PACKAGE.parents[1])
    monkeypatch.setattr(public, "bundled_srd_archive", _archive)
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: RUNTIME["definitions"]["spells"])
    monkeypatch.setattr(fall, "_runtime", lambda: (RUNTIME, ENCODED))
    monkeypatch.setattr(fall, "_archive", _archive)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)


def _commit(owner, name, **params):
    result, command = _act(owner, name, **params)
    assert result.status is ActionStatus.COMMITTED, result.result
    return result, command


def _scene(state, subject):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == subject
    )


def _prepare_npc(state, actor, spells):
    if actor not in {r["id"] for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}:
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity(actor, [0, 5, 0], participant_ref=actor)
        )
    materials = [
        m
        for spell in spells
        for m in cards._spell_case(spell)[0][f"{SRD}.entities"]["hero"]["spell_materials"]
    ]
    budget._run(
        "prepare_srd_creature",
        state,
        creature_id=actor,
        name="Prepared test wizard",
        source="Synthetic wizard: AC13 HP1000 Speed30 Int3 attack7 DC15 Con0.",
        armor_class=13,
        hit_points=1000,
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
        spells=[{"spell": spell, "uses_per_day": 2} for spell in spells],
    )


def test_existing_paid_fine_control_journey_uses_selected_final_owners(tmp_path, monkeypatch):
    # Preserve the owning public journey's actual save, costs, geometry and retries.
    fine.test_paid_public_fine_control_admits_facts_reopens_and_retries_without_another_slot(
        None, tmp_path, monkeypatch
    )


def test_real_pending_cast_blocks_hand_and_fine_edits_atomically_then_completes(
    tmp_path, monkeypatch
):
    pending = {}

    def case(_spell):
        state, data, params = fall._cast_case("telekinesis")
        params["telekinesis_destination"] = [25, 5, 0]
        materials = doors.ward._add_spell(state, data, "false_life")
        actor = state[f"{SRD}.entities"]["hero"]
        actor["inventory"].append("Torch")
        pending.update(cards._spell_case("false_life")[2] | {"material_item_ids": materials})
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", case)
    path = tmp_path / "pending-fine.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "telekinesis", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        _commit(owner, "record_srd_telekinesis_object_size", object_id="orb", size="small")
        _commit(
            owner,
            "record_srd_object_access",
            object_id="orb",
            object_kind="door",
            closed=True,
            locks=[],
            stuck=False,
            barred=False,
        )
        cast, cast_command = _commit(owner, "cast_spell_outside_combat", **params)
        _commit(owner, "record_srd_control_held_items", target_state="hero", held_items=["Torch"])
        hands, hands_command = _commit(
            owner,
            "record_srd_scene_hand_occupancy",
            actor_state="hero",
            hand_capacity=2,
            holdings=[{"inventory_item": "Torch", "hand_indices": [0]}],
        )
        _commit(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        begun, begin_command = _commit(owner, "begin_srd_spell_action", **pending)
        saved = owner.snapshot("casting")
        lasting = saved.state[f"{SRD}.state"]
        action_id = lasting["pending_srd_action_stack"][-1]
        source = lasting["telekinesis_sources"][-1]
        for name, arguments in (
            (
                "record_srd_scene_hand_occupancy",
                {
                    "actor_state": "hero",
                    "hand_capacity": 2,
                    "holdings": [{"inventory_item": "Torch", "hand_indices": [0, 1]}],
                },
            ),
            (
                "operate_srd_telekinesis_object",
                {
                    "caster_state": "hero",
                    "source_id": source["id"],
                    "object_id": "orb",
                    "operation": "open",
                },
            ),
        ):
            with pytest.raises(MechanicsRejected, match="pending reaction window"):
                _act(owner, name, **arguments)
            assert owner.snapshot("casting") == saved
        assert owner.execute_action(begin_command) == begun
        assert owner.execute_action(cast_command) == cast
        assert owner.execute_action(hands_command) == hands
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        completed, complete_command = _commit(
            owner, "complete_srd_pending_action", pending_action_id=action_id
        )
        final = owner.snapshot("casting")
        assert not final.state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert _scene(final.state, "orb")["metadata"]["srd_object_access"]["closed"] is True
        assert _scene(final.state, "hero")["metadata"]["srd_hand_occupancy"]["holdings"] == [
            {"inventory_item": "Torch", "hand_indices": [0]}
        ]
        assert owner.execute_action(complete_command) == completed
        assert owner.snapshot("casting") == final
        _commit(
            owner,
            "record_srd_scene_hand_occupancy",
            actor_state="hero",
            hand_capacity=2,
            holdings=[{"inventory_item": "Torch", "hand_indices": [0, 1]}],
        )
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert _scene(owner.snapshot("casting").state, "hero")["metadata"]["srd_hand_occupancy"][
            "holdings"
        ] == [{"inventory_item": "Torch", "hand_indices": [0, 1]}]


def test_real_ward_and_independent_lock_suppression_preserves_fine_access_and_sources(
    tmp_path, monkeypatch
):
    materials = {}

    def case(_spell):
        state, data, params = doors._case()
        materials["arcane_lock"] = doors.ward._add_spell(state, data, "arcane_lock")
        materials["antimagic_field"] = doors.ward._add_spell(state, data, "antimagic_field")
        _prepare_npc(state, "fine-mage", ["telekinesis"])
        _scene(state, "fine-mage")["position"] = [0, 1, 0]
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", case)
    path = tmp_path / "ward-fine.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "guards_and_wards", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        _commit(owner, "record_srd_telekinesis_object_size", object_id="door", size="small")
        _commit(owner, "record_srd_telekinesis_object_size", object_id="orb", size="small")
        _commit(
            owner,
            "record_srd_object_access",
            object_id="orb",
            object_kind="container",
            closed=True,
            locks=[],
            stuck=False,
            barred=False,
        )
        _commit(owner, "begin_srd_spell_cast", **doors.ward._begin(params))
        _commit(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        ward_cast, ward_command = _commit(owner, "cast_spell_outside_combat", **params)
        lock, lock_command = _commit(
            owner,
            "cast_spell_outside_combat",
            **(
                params
                | {
                    "spell": "arcane_lock",
                    "slot_resource": "spell_slot_2",
                    "material_item_ids": materials["arcane_lock"],
                    "object_target_id": "door",
                    "object_password": "independent",
                }
            ),
        )
        tk = deepcopy(cards._spell_case("telekinesis")[2])
        tk.pop("caster", None)
        tk.pop("slot_resource", None)
        tk.update(
            caster_state="fine-mage",
            telekinesis_subject="orb",
            telekinesis_destination=[25, 0, 0],
            material_item_ids=[],
        )
        tk_cast, tk_command = _commit(owner, "cast_spell_outside_combat", **tk)
        _commit(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        saved = owner.snapshot("casting")
        sid = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]["id"]
        source_before = deepcopy(saved.state[f"{SRD}.state"]["telekinesis_sources"][-1])
        assert source_before["active"] is True
        assert (
            saved.state[f"{SRD}.entities"]["fine-mage"]["spell_uses_remaining"]["telekinesis"] == 1
        )
        for name, args in (
            (
                "operate_srd_locked_object",
                {"actor_state": "guard", "object_id": "door", "operation": "open", "password": ""},
            ),
            (
                "operate_srd_telekinesis_object",
                {
                    "caster_state": "fine-mage",
                    "source_id": sid,
                    "object_id": "door",
                    "operation": "open",
                },
            ),
        ):
            with pytest.raises(MechanicsRejected, match="Arcane Lock prevents"):
                _act(owner, name, **args)
            assert owner.snapshot("casting") == saved
        anti, anti_command = _commit(
            owner,
            "cast_spell_outside_combat",
            **(
                params
                | {
                    "spell": "antimagic_field",
                    "target": "hero",
                    "slot_resource": "spell_slot_8",
                    "material_item_ids": materials["antimagic_field"],
                }
            ),
        )
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        opened, open_command = _commit(
            owner,
            "operate_srd_locked_object",
            actor_state="guard",
            object_id="door",
            operation="open",
            password="",
        )
        _commit(
            owner,
            "operate_srd_locked_object",
            actor_state="guard",
            object_id="door",
            operation="close",
            password="",
        )
        before_fine = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            _act(
                owner,
                "operate_srd_telekinesis_object",
                caster_state="fine-mage",
                source_id=sid,
                object_id="door",
                operation="open",
            )
        assert owner.snapshot("casting") == before_fine
        ended, end_command = _commit(owner, "end_concentration", caster_state="hero")
        stopped = owner.snapshot("casting")
        for name, args in (
            (
                "operate_srd_locked_object",
                {"actor_state": "guard", "object_id": "door", "operation": "open", "password": ""},
            ),
            (
                "operate_srd_telekinesis_object",
                {
                    "caster_state": "fine-mage",
                    "source_id": sid,
                    "object_id": "door",
                    "operation": "open",
                },
            ),
        ):
            with pytest.raises(MechanicsRejected, match="Arcane Lock prevents"):
                _act(owner, name, **args)
            assert owner.snapshot("casting") == stopped
        assert stopped.state[f"{SRD}.state"]["telekinesis_sources"][-1] == source_before
        assert stopped.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        assert stopped.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert stopped.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        assert (
            stopped.state[f"{SRD}.entities"]["fine-mage"]["spell_uses_remaining"]["telekinesis"]
            == 1
        )
        for command, receipt in (
            (ward_command, ward_cast),
            (lock_command, lock),
            (tk_command, tk_cast),
            (anti_command, anti),
            (open_command, opened),
            (end_command, ended),
        ):
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == stopped
        outside, outside_command = _commit(
            owner,
            "operate_srd_telekinesis_object",
            caster_state="fine-mage",
            source_id=sid,
            object_id="orb",
            operation="open",
        )
        final = owner.snapshot("casting")
        assert _scene(final.state, "orb")["metadata"]["srd_object_access"]["closed"] is False
        assert _scene(final.state, "orb")["position"] == [25, 0, 0]
        assert final.state[f"{SRD}.state"]["telekinesis_sources"][-1]["active"] is True
        assert final.state[f"{SRD}.state"]["telekinesis_sources"][-1]["id"] == sid
        assert (
            final.state[f"{SRD}.entities"]["fine-mage"]["concentration_spell_id"] == "telekinesis"
        )
        assert (
            final.state[f"{SRD}.entities"]["fine-mage"]["spell_uses_remaining"]["telekinesis"] == 1
        )
        assert (
            final.state[f"{SRD}.entities"]["hero"]["resources"]
            == stopped.state[f"{SRD}.entities"]["hero"]["resources"]
        )
        assert owner.execute_action(outside_command) == outside
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
