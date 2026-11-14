"""Paid Feather Fall persists its actual saved event, source and later physical descent."""

import json
from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.srd_feather_fall import bind_runtime_consumers
from scripts.srd_full.srd_reaction_spells import extend_runtime as extend_reactions
from scripts.srd_full.srd_reaction_spells import normalize_pending_spell_guards
from scripts.srd_full.srd_spell_source_instances import extend_runtime as extend_sources
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _srd_drafts as _base_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from tests import test_srd_feather_fall as falling
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_appearance as appearance
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage


def _linked_drafts():
    """Retain every existing artifact link with its exact public payload during admission."""
    primary, reference = _base_drafts()
    payload = dict(primary.payloads)
    declarations = json.loads(payload["content/content-declarations.json"])
    root = bundled_packages._bundled_srd_root()
    for inclusion in declarations["inclusions"]:
        for path in inclusion.get("links", {}).get("artifacts", []):
            if path not in payload:
                payload[path] = (root / path).read_bytes().replace(b"\r\n", b"\n")
    return NormalizedPackageDraft(primary.record, payload, 4), reference


@lru_cache
def _public_runtime():
    runtime, content = falling._runtime()
    text = extend_sources(
        normalize_pending_spell_guards(content.decode()),
        requirements=runtime["definitions"]["spell_casting"],
        definitions=runtime["definitions"]["spells"],
    )
    text = bind_runtime_consumers(extend_reactions(text))
    runtime = _load_runtime(text)
    return runtime, yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False).encode()


def _public_case(spell):
    assert spell == "feather_fall"
    state, data, parameters = falling._case(height=120)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    body = deepcopy(scene["entities"][0])
    body["id"] = "mage"
    body["metadata"]["participant_ref"] = "mage"
    scene["entities"].append(body)
    return state, data, parameters


@pytest.mark.parametrize("npc", [False, True])
def test_strict_paid_public_event_cast_reopen_retry_and_later_landing(tmp_path, monkeypatch, npc):
    runtime, _ = _public_runtime()
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(bundled_packages, "_srd_drafts", _linked_drafts)
    monkeypatch.setattr(appearance, "_runtime", _public_runtime)
    monkeypatch.setattr(appearance, "PUBLIC_TOOLS", {"begin_srd_scene_fall"})
    appearance._appearance_archive.cache_clear()
    monkeypatch.setattr(public, "bundled_srd_archive", appearance._appearance_archive)
    monkeypatch.setattr(public, "_spell_case", _public_case)
    database = tmp_path / "feather.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner, "feather_fall", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        if npc:
            hero = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
            materials = [
                {
                    key: list(item[key]) if key == "components" else item[key]
                    for key in (
                        "id",
                        "inventory_item",
                        "kind",
                        "components",
                        "quantity",
                        "value_cp",
                    )
                }
                for item in hero["spell_materials"]
            ]
            prepared, _ = _prepare_mage(
                owner,
                [{"spell": "feather_fall", "uses_per_day": 2}],
                inventory=[item["inventory_item"] for item in materials],
                spell_materials=materials,
            )
            assert prepared.status is ActionStatus.COMMITTED
            parameters.pop("caster")
            parameters.pop("slot_resource")
            parameters["caster_state"] = "mage"
        caster = "mage" if npc else "hero"
        assert "begin_srd_scene_fall" in {
            tool.name for tool in StatelessMcpMechanics(owner).list_tools("casting")
        }
        begun, begin_command = _act(
            owner,
            "begin_srd_scene_fall",
            target_states=["guard"],
            fall_cause="departure",
        )
        assert begun.status is ActionStatus.COMMITTED
        event_snapshot = owner.snapshot("casting")
        trigger = event_snapshot.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(begin_command) == begun
        assert owner.snapshot("casting").state == event_snapshot.state
        parameters.update(targets=["guard"], reaction_trigger_id=trigger)
        paid, cast_command = _act(owner, "cast_srd_reaction_spell", **parameters)
        assert paid.status is ActionStatus.COMMITTED
        cast_snapshot = owner.snapshot("casting")
        actor = cast_snapshot.state[f"{SRD}.entities"][caster]
        if npc:
            assert actor["spell_uses_remaining"]["feather_fall"] == 1
            assert actor["resources"] == {}
        else:
            assert actor["resources"]["spell_slot_1"] == 2
        assert actor["reaction_available"] is False
        source = next(
            row
            for row in cast_snapshot.state[f"{SRD}.state"]["turn_effects"]
            if row.get("kind") == "feather_fall"
        )
        assert source["cast_id"] and source["source_caster_id"] == caster
        assert source["target"] == "guard" and source["spell_level"] == 1
        assert falling._height(cast_snapshot.state) == 120
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(cast_command) == paid
        assert owner.snapshot("casting").state == cast_snapshot.state
        finished, finish_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=trigger
        )
        assert finished.status is ActionStatus.COMMITTED
        completed = owner.snapshot("casting")
        assert falling._actor(completed.state)["scene_fall"]["status"] == "descending"
        assert completed.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert owner.execute_action(finish_command) == finished
        assert owner.snapshot("casting").state == completed.state
        elapsed, elapsed_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        assert elapsed.status is ActionStatus.COMMITTED
        descending = owner.snapshot("casting")
        assert falling._height(descending.state) == 60
        assert falling._actor(descending.state)["hp"] == 1000
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.execute_action(elapsed_command) == elapsed
        assert owner.snapshot("casting").state == descending.state
        landed, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        assert landed.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting").state
        assert falling._height(final) == 0 and falling._actor(final)["hp"] == 1000
        assert falling._actor(final)["scene_fall"]["status"] == "landed"
        assert not any(
            row.get("kind") == "feather_fall" and row["target"] == "guard" and row["active"]
            for row in final[f"{SRD}.state"]["turn_effects"]
        )
