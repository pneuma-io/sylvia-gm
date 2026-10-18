"""Exact parent removal uses real setup, paid Dispel, inventory loss and saved state."""

import json
import os
import re
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_contingency_removal import bind_runtime
from scripts.srd_spell_removal_adoption import _bind_selected_recovery
from scripts.srd_timed_actor_defenses import _load_runtime
from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from sylvia_gm.spatial import admit_spatial_scene

from tests import test_srd_contingency_spell as parent
from tests import test_srd_public_spell_casting as public_casting
from tests.test_srd_npc_spell_casting import _act

force, ROOT, SRD, POOL = parent.force, parent.ROOT, parent.SRD, parent.POOL


@lru_cache
def _runtime():
    supplied = os.environ.get("FC14_CONTINGENCY_REMOVAL_RUNTIME")
    if supplied:
        text = bind_runtime(Path(supplied).read_text())
        assert bind_runtime(text) == text
        runtime = _load_runtime(text)
        assert not (errors := validate_ruleset_data(runtime)), errors
        return text.encode(), runtime
    # Reuse the proven Contingency1 setup graph, adding only existing removal owners.
    runtime = deepcopy(parent._runtime()[1])
    native = _load_runtime(parent.BASE.read_text())
    tools = native["tools"] | runtime["tools"]
    runtime["tools"] = tools
    _bind_selected_recovery(tools)
    text = bind_runtime(yaml.dump(runtime, Dumper=parent.NoAliases, sort_keys=False))
    assert bind_runtime(text) == text
    runtime = _load_runtime(text)
    runtime["tools"]["cast_spell_outside_combat"] = deepcopy(runtime["tools"]["cast_spell"])
    selected, pending = (
        {},
        [
            "cast_spell",
            "cast_spell_outside_combat",
            "dismiss_srd_spell",
            "cast_srd_recovery",
            "dispel_srd_contingency_owned_source",
            "collect_srd_contingency_owned_cast",
            "record_srd_control_held_items",
            "drop_srd_control_held_items",
            "begin_srd_spell_cast",
            "continue_srd_spell_cast",
            "apply_srd_qualifying_damage",
            "drain_srd_contingency_releases",
            "check_srd_contingency_source",
        ],
    )
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "call" in node:
                    pending.append(node["call"])
                for kind in ("spatial_query", "spatial_mutation"):
                    if kind in node:
                        node[kind]["scene"] = re.sub(
                            r"^lasting_state\.", "state.", node[kind]["scene"]
                        )
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    runtime["tools"] = selected
    assert not (errors := validate_ruleset_data(runtime)), errors
    raw = yaml.dump(runtime, Dumper=parent.NoAliases, sort_keys=False).encode()
    (ROOT / ".scratch/contingency-removal-proof-runtime.yaml").write_bytes(raw)
    return raw, runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return force.materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="d" * 64,
        runtime_bytes=raw,
        declaration=force.ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=force.owned_payload(
                force._tool_schema(runtime["tools"][name], tool_name=name)
            ),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    from sylvia_gm.application import package_runtime as package

    original = package.execute_tool

    def checked(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print("NATIVE", args[0], result.failure_kind, result.error)
        return result

    monkeypatch.setattr(package, "execute_tool", checked)
    monkeypatch.setattr(force.objects, "_mechanic", _mechanic)
    monkeypatch.setattr(force.actions, "_mechanic", _mechanic)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    if os.environ.get("FC14_CONTINGENCY_REMOVAL_RUNTIME"):
        monkeypatch.setattr(parent, "_runtime", _runtime)


def _cast(monkeypatch):
    # Read the complete current scene before parent._state temporarily binds its headers.
    raw_scene = force.objects._state()[f"{SRD}.state"]["spatial_scene"]
    scene = admit_spatial_scene(raw_scene)
    state, data, params = parent._state(monkeypatch)
    assert state[f"{SRD}.state"].get("spatial_scene") is None
    state[f"{SRD}.state"]["spatial_scene"] = scene.as_payload()
    assert admit_spatial_scene(state[f"{SRD}.state"]["spatial_scene"]).snapshot_id == (
        scene.snapshot_id
    )
    sid = parent._finish_setup(state, data, params)
    for collection in (
        data["wizard_spellbook"],
        data["spellcasting_grants"][0]["spells"],
        state[f"{SRD}.entities"]["hero"]["prepared_spells"],
    ):
        collection.append("dispel_magic")
    return state, data, params, sid


def _dispel_params(params, sid):
    return params | {
        "spell": "dispel_magic",
        "target": "hero",
        "slot_resource": "spell_slot_3",
        "target_distance": 0,
        "material_item_ids": [],
        "recovery_choice": "contingency",
        "recovery_source_id": sid,
    }


def test_actual_common_dismissal_ends_only_armed_parent_and_preserves_released_child(monkeypatch):
    state, data, params, first = _cast(monkeypatch)
    parent._damage(state, 10)
    temp_hp = state[f"{SRD}.entities"]["hero"]["temp_hp"]
    assert temp_hp > 0 and state[f"{SRD}.state"][POOL][first]["status"] == "released"
    second = parent._finish_setup(state, data, params)
    before = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    reopened = json.loads(json.dumps(state))
    force.objects._run("dismiss_srd_spell", reopened, caster_state="hero", cast_id=second)
    record = reopened[f"{SRD}.state"][POOL][second]
    assert record["installed"] is False and record["end_reason"] == "dismissed"
    assert reopened[f"{SRD}.state"][POOL][first]["status"] == "released"
    assert reopened[f"{SRD}.entities"]["hero"]["temp_hp"] == temp_hp
    assert reopened[f"{SRD}.entities"]["hero"]["resources"] == before
    saved = deepcopy(reopened)
    with pytest.raises(RulesetPreconditionRejected):
        force.objects._run("dismiss_srd_spell", reopened, caster_state="hero", cast_id=second)
    assert reopened == saved


def test_paid_lower_slot_dispel_retains_failed_source_then_ends_exact_source_on_success(
    monkeypatch,
):
    state, data, params, sid = _cast(monkeypatch)
    slot_before = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"]
    monkeypatch.setattr(dice, "_rng", Random(2))
    failed = force.actions._run(
        "cast_spell_outside_combat", state, participant_data=data, **_dispel_params(params, sid)
    )
    assert failed.result["status"] == "committed"
    assert "selected 2" in str(failed.result["result_card"])
    assert "DC 16" in str(failed.result["result_card"])
    assert state[f"{SRD}.state"][POOL][sid]["installed"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == slot_before - 1
    reopened = json.loads(json.dumps(state))
    monkeypatch.setattr(dice, "_rng", Random(0))
    succeeded = force.actions._run(
        "cast_spell_outside_combat", reopened, participant_data=data, **_dispel_params(params, sid)
    )
    assert "selected 13" in str(succeeded.result["result_card"])
    assert reopened[f"{SRD}.state"][POOL][sid]["end_reason"] == "dispel"
    assert reopened[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == slot_before - 2
    assert "DC 16" in str(succeeded.result["result_card"])


def test_actual_statuette_drop_ends_source_at_same_inventory_boundary(monkeypatch):
    state, _, _, sid = _cast(monkeypatch)
    item = state[f"{SRD}.state"][POOL][sid]["statuette_item"]
    force.objects._run(
        "record_srd_control_held_items",
        state,
        target_state="hero",
        held_items=[item],
        held_attack_names=[],
    )
    force.objects._run("drop_srd_control_held_items", state, target_state="hero")
    assert state[f"{SRD}.state"][POOL][sid]["installed"] is False
    assert state[f"{SRD}.entities"]["hero"]["has_contingency"] is False
    reopened = json.loads(json.dumps(state))
    reopened[f"{SRD}.entities"]["hero"]["inventory"].append(item)
    parent._damage(reopened, 10)
    assert reopened[f"{SRD}.entities"]["hero"]["temp_hp"] == 0


def test_actual_current_body_path_rejects_unreachable_source_without_cost(monkeypatch):
    state, _, _, sid = _cast(monkeypatch)
    state[f"{SRD}.state"]["spatial_scene"]["map"]["bounds"]["max"][0] = 400
    for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if body["id"] == "guard":
            body["position"] = [300, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="beyond Dispel range"):
        force.objects._run(
            "dispel_srd_contingency_owned_source",
            state,
            caster_state="guard",
            source_id=sid,
            slot_level=3,
            casting_modifier=0,
            casting_ability="intelligence",
        )
    assert state == before


def test_suppressed_source_remains_removable_and_foreign_dismissal_rejects(monkeypatch):
    state, _, _, sid = _cast(monkeypatch)
    state[f"{SRD}.state"][POOL][sid]["suppressed"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        force.objects._run("dismiss_srd_spell", state, caster_state="guard", cast_id=sid)
    assert state == before
    force.objects._run("dismiss_srd_spell", state, caster_state="hero", cast_id=sid)
    assert state[f"{SRD}.state"][POOL][sid]["installed"] is False


@lru_cache
def _test_archive():
    """Admit only this selected public proof, not a replacement canonical package."""
    primary, reference = _srd_drafts()
    raw, runtime = _runtime()
    record = deepcopy(primary.record)
    record["ui_surfaces"] = []
    public = {"cast_spell_outside_combat"}
    record["tools"] = [
        {
            "name": name,
            "capability": "dnd.srd.5.2.1.spells",
            "input_schema": force._tool_schema(runtime["tools"][name], tool_name=name),
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
        }
        for name in sorted(public)
    ]
    payload = dict(primary.payloads) | {"runtime/ruleset.yaml": raw}
    content = json.loads(payload["content/content-declarations.json"])
    for inclusion in content["inclusions"]:
        links = inclusion.get("links", {})
        links["tools"] = [n for n in links.get("tools", []) if n in public]
        links["ui_surfaces"] = []
    content["inclusions"].append(
        {
            "id": "synthetic-contingency-removal-proof",
            "kind": "ruleset_runtime",
            "family": "contingency-removal-proof",
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
                "tools": sorted(public),
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["selected-paid-contingency-dispel-reopen-retry"]},
        }
    )
    payload["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payload, primary.format_version), reference))


def test_public_paid_failed_and_successful_dispel_reopen_exact_retry(tmp_path, monkeypatch):
    state, data, params, sid = _cast(monkeypatch)
    before_slots = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"]
    database = tmp_path / "contingency-removal.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
        create = owner.create_campaign

        def admit(command):
            participant = replace(command.participants[0], data=command.participants[0].data | data)
            initial = tuple(
                force.objects.StateValue(SRD, k.removeprefix(f"{SRD}."), v)
                for k, v in state.items()
            )
            return create(replace(command, participants=(participant,), initial_state=initial))

        from unittest.mock import patch

        with patch.object(owner, "create_campaign", side_effect=admit):
            public_casting._create_spell_campaign(owner, parent.SPELL)
        monkeypatch.setattr(dice, "_rng", Random(2))
        failed, failed_command = _act(
            owner, "cast_spell_outside_combat", **_dispel_params(params, sid)
        )
        assert failed.status is ActionStatus.COMMITTED, failed.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.state"][POOL][sid]["installed"] is True
        assert (
            saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == before_slots - 1
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(failed_command) == failed
        assert owner.snapshot("casting") == saved
        monkeypatch.setattr(dice, "_rng", Random(0))
        succeeded, command = _act(owner, "cast_spell_outside_combat", **_dispel_params(params, sid))
        assert succeeded.status is ActionStatus.COMMITTED, succeeded.result
        settled = owner.snapshot("casting")
        assert settled.state[f"{SRD}.state"][POOL][sid]["installed"] is False
        assert (
            settled.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"]
            == before_slots - 2
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Exact paid retry rerolled"))
        assert owner.execute_action(command) == succeeded
        assert owner.execute_action(failed_command) == failed
        assert owner.snapshot("casting") == settled
