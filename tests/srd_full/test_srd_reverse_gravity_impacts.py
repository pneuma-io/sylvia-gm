"""One paid Gravity impact and ended-source object landing use existing receiving owners."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_reverse_gravity import (
    PROFILES,
    PUBLIC_TOOLS,
    bind_runtime_consumers,
    extend_runtime,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel import _case as spell_case
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case


@lru_cache
def _runtime():
    base = Path(".scratch/celestial-initial-sight-frozen-v1/excluded/runtime.yaml").read_bytes()
    assert (
        hashlib.sha256(base).hexdigest()
        == "65498ad3fe2bdf8b41bd30b4e25ba703c181fe5498b32c460bd5377688fe1045"
    )
    r = _load_runtime(bind_runtime_consumers(extend_runtime(base.decode())))
    r["definitions"]["spells"].update(deepcopy(PROFILES))
    for name in (
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        for p in r["tools"].get(name, {}).get("parameters", []):
            if p["name"] == "spell" and "enum_values" in p:
                p["enum_values"] = sorted(set(p["enum_values"]) | PROFILES.keys())
    encoded = yaml.dump(r, Dumper=yaml.CSafeDumper, sort_keys=False).encode()
    return r, encoded


@pytest.fixture
def gravity_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    drafts = bundled_packages._srd_drafts

    def declared():
        primary, reference = drafts()
        record = deepcopy(primary.record)
        payload = dict(primary.payloads)
        names = {row["name"] for row in record["tools"]}
        for name in sorted(PUBLIC_TOOLS - names):
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
        declaration = json.loads(payload["content/content-declarations.json"])
        for inclusion in declaration["inclusions"]:
            if "cast_spell" in inclusion.get("links", {}).get("tools", []):
                inclusion["links"]["tools"] = sorted(
                    set(inclusion["links"]["tools"]) | PUBLIC_TOOLS
                )
        payload["content/content-declarations.json"] = canonical_json_bytes(declaration)
        return NormalizedPackageDraft(record, payload, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", declared)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case():
    state, data = _tk_case("reverse_gravity")
    _, _, params = spell_case("reverse_gravity")
    params.pop("targets", None)
    params["gravity_origin"] = [50, 0, 0]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(b for b in scene["entities"] if b["id"] == "hero")["position"] = [-15, 0, 60]
    next(b for b in scene["entities"] if b["id"] == "orb")["position"] = [8, 0, 0]
    scene["geometry"].append(
        {
            "id": "caster-platform",
            "map_id": "information",
            "volume": {"kind": "box", "min": [-20, -5, 59], "max": [-10, 5, 60]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": True,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    for grant in data["spellcasting_grants"]:
        if grant["source"] == "wizard":
            grant["spells"].append("feather_fall")
    data["wizard_spellbook"].append("feather_fall")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("feather_fall")
    state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] = 3
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 0
    feather_state, _, _ = spell_case("feather_fall")
    for material in feather_state[f"{SRD}.entities"]["hero"]["spell_materials"]:
        material = deepcopy(material)
        material["id"] = "feather_" + material["id"]
        state[f"{SRD}.entities"]["hero"]["spell_materials"].append(material)
        state[f"{SRD}.entities"]["hero"]["inventory"].append(material["inventory_item"])
    return state, data, params


def _paid_case(_spell):
    state, data, params = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for body in scene["entities"]:
        body["metadata"]["reverse_gravity_ground_anchor"] = body["id"] not in {"guard", "orb"}
        body["metadata"]["reverse_gravity_grabbable"] = False
    scene["geometry"].append(
        {
            "id": "ceiling",
            "map_id": "information",
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "occupies_cells": True,
            "volume": {"kind": "box", "min": [4, -1, 40], "max": [6, 1, 41]},
            "metadata": {"plane": "material"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    return state, data, params


def test_paid_gravity_impact_and_object_landing_save_reopen_retry(
    gravity_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _paid_case)
    game = _TravelDice([(6, 1)] * 6)
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "gravity.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "reverse_gravity", target_overrides={"hp": 500, "max_hp": 500}
        )
        base = ActionCommand(
            campaign_id="casting",
            action_id="cast",
            idempotency_key="cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )

        def act(identity, name, selected, status=ActionStatus.COMMITTED):
            command = replace(
                base,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=selected,
            )
            if status == "rejected":
                before = owner.snapshot("casting")
                with pytest.raises(MechanicsRejected):
                    owner.execute_action(command)
                assert owner.snapshot("casting").state == before.state
                return None, owner.snapshot("casting")
            result = owner.execute_action(command)
            assert result.status is status, result.result
            saved = owner.snapshot("casting")
            assert owner.execute_action(command) == result and owner.snapshot("casting") == saved
            return result, saved

        before = owner.snapshot("casting")
        # Wrong observed distance cannot mutate a fact, spend a slot, or move the body.
        act(
            "invalid-fact",
            "record_srd_reverse_gravity_anchoring",
            {
                "subject": "guard",
                "anchored": False,
                "grabbable": False,
                "fall_direction": "up",
                "fall_distance": 30,
                "fall_obstacle_id": "ceiling",
            },
            "rejected",
        )
        assert owner.snapshot("casting").state == before.state
        _, facts = act(
            "impact-fact",
            "record_srd_reverse_gravity_anchoring",
            {
                "subject": "guard",
                "anchored": False,
                "grabbable": False,
                "fall_direction": "up",
                "fall_distance": 34,
                "fall_obstacle_id": "ceiling",
            },
        )
        slot = facts.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"]
        _, raised = act("cast", "cast_spell_outside_combat", params)
        assert _point(raised.state, "guard") == [5, 0, 34]
        assert _point(raised.state, "orb") == [8, 0, 100]
        assert raised.state[f"{SRD}.entities"]["guard"]["hp"] == 497
        assert "prone" in raised.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert raised.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == slot - 1
        source = raised.state[f"{SRD}.state"]["reverse_gravity_sources"][-1]
        guard = next(row for row in source["subjects"] if row["subject"] == "guard")
        assert (
            guard["impact_distance"] == 34
            and guard["impact_obstacle"] == "ceiling"
            and guard["impact_damage"] == 3
        )
        _, ended = act("expiry", "advance_srd_lasting_time", {"elapsed_rounds": 10})
        assert ended.state[f"{SRD}.state"]["reverse_gravity_sources"][-1]["return_pending"] is True
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == ended
        before = owner.snapshot("casting")
        act(
            "missing-landing",
            "settle_srd_reverse_gravity_returns",
            {"source_id": source["id"]},
            "rejected",
        )
        assert owner.snapshot("casting").state == before.state
        act(
            "landing-fact",
            "record_srd_reverse_gravity_anchoring",
            {
                "subject": "orb",
                "anchored": False,
                "grabbable": False,
                "fall_direction": "down",
                "fall_distance": 100,
                "fall_obstacle_id": "ground",
            },
        )
        opened, pending = act(
            "settle", "settle_srd_reverse_gravity_returns", {"source_id": source["id"]}
        )
        assert _point(pending.state, "orb") == [8, 0, 0]
        action = opened.result["context"]["_pending_action_id"]
        assert pending.state[f"{SRD}.state"]["pending_srd_actions"][action]["kind"] == "scene_fall"
        assert (
            pending.state[f"{SRD}.state"]["reverse_gravity_sources"][-1]["return_pending"] is False
        )
        _, landed = act("complete", "complete_srd_pending_action", {"pending_action_id": action})
        assert _point(landed.state, "guard") == [5, 0, 0]
        assert landed.state[f"{SRD}.entities"]["guard"]["hp"] == 494
        assert landed.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == slot - 1
        assert game.consumed == [(6, 1)] * 6 and not game.rolls
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == landed
