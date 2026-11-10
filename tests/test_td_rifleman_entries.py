"""Owner-approved provisional RIFLEMAN entries execute through admitted procedures."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_exposure_owner import create
from tests.test_td_ordinary_pose import NS, session, tool_call
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import PackageProcedures

candidate = candidate_fixture
NODES = {
    "gunner": "specialty.gunner.entry.sustained_fire_discipline",
    "operator": "specialty.operator.entry.weapon_manipulation",
}
OUT = Path(__file__).resolve().parent


def scene_for(candidate, node, mirrored, enabled, negative=False):
    scene = PackageProcedures(candidate, mirrored)
    scene.shooter, scene.mover = "vesper", "hostile_alpha"
    actor = scene.actors[scene.shooter]
    assert actor["class_id"] == "RIFLEMAN" and not actor["specialties"]
    if enabled:
        actor["specialties"] = [NODES[node]]
    if negative:
        if node == "gunner":
            actor["weapon"]["chamber"] = "EMPTY"
        else:
            actor["weapon"]["spare_magazines"] = []
    return scene


@pytest.fixture(
    scope="module",
    params=[("gunner", False), ("gunner", True), ("operator", False), ("operator", True)],
)
def owning_pair(request, candidate, tmp_path_factory):
    node, mirrored = request.param
    path = tmp_path_factory.mktemp(f"{node}-{mirrored}")
    database, exported = path / "authority.sqlite3", path / "entry-node.sylvia-gm-campaign"
    arms, saved, callbacks = {}, {}, {}
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        owner.set_admin_full_audit(True)
        for enabled in (False, True):
            label = "node" if enabled else "baseline"
            scene = scene_for(candidate, node, mirrored, enabled)
            game = create(owner, admitted, scene, campaign=label)
            session(owner, game)
            initial = thaw_json_value(owner.snapshot(label).state[NS + ".entities"][scene.shooter])
            exposure = (
                SpatialEngine(game.world["spatial_scene"])
                .query(
                    {
                        "kind": "exposure",
                        "map_id": game.world["spatial_scene"]["map"]["id"],
                        "observer": {"point": initial["muzzle"]},
                        "target": {"entity_id": scene.mover},
                        "samples": {"horizontal": 3, "vertical": 3},
                        "include": ["geometry", "entities"],
                    }
                )
                .as_payload()
            )
            assert exposure["exposure"] > 0
            commands = []
            snapshots = [initial]
            rounds = 2 if node == "gunner" else 1
            for index in range(rounds):
                call = tool_call(
                    owner,
                    game,
                    "fire_single",
                    {
                        "attacker": scene.shooter,
                        "target": scene.mover,
                        "aim_point": game.actors[scene.mover]["center"],
                        "critical_effect": "DEVASTATING_HIT",
                    },
                    f"fire-{index}",
                )
                with patch.object(dice._rng, "randint", return_value=1) as draws:
                    receipt = call()
                assert str(receipt.status) == "committed" and draws.call_count == 1
                commands.append((call, receipt))
                snapshots.append(thaw_json_value(game.actors[scene.shooter]))
            if node == "operator":
                call = tool_call(
                    owner,
                    game,
                    "reload_weapon",
                    {"actor": scene.shooter, "disposition": "RETAIN"},
                    "reload",
                )
                with patch.object(dice._rng, "randint") as draws:
                    receipt = call()
                assert str(receipt.status) == "committed" and draws.call_count == 0
                commands.append((call, receipt))
                snapshots.append(thaw_json_value(game.actors[scene.shooter]))
            saved[label] = owner.snapshot(label)
            callbacks[label] = commands
            arms[label] = snapshots
            if enabled:
                owner.export_campaign("entry-export", label, exported)
        negative = create(
            owner, admitted, scene_for(candidate, node, mirrored, True, True), campaign="negative"
        )
        session(owner, negative)
        before_negative = deepcopy(negative.actors[negative.shooter])
        if node == "gunner":
            reject = tool_call(
                owner,
                negative,
                "fire_single",
                {
                    "attacker": negative.shooter,
                    "target": negative.mover,
                    "aim_point": negative.actors[negative.mover]["center"],
                    "critical_effect": "DEVASTATING_HIT",
                },
                "negative-fire",
            )
        else:
            reject = tool_call(
                owner,
                negative,
                "reload_weapon",
                {"actor": negative.shooter, "disposition": "RETAIN"},
                "negative-reload",
            )
        with patch.object(dice._rng, "randint") as draws:
            reject()
        assert draws.call_count == 0 and negative.actors[negative.shooter] == before_negative
        negative_status = (
            negative.world["last_attack"]["outcome"]
            if node == "gunner"
            else negative.world["last_reload"]["status"]
        )
    with AuthorityOwner.open(database, "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        for label, expected in saved.items():
            assert owner.snapshot(label) == expected
            with patch.object(dice._rng, "randint") as draws:
                for call, receipt in callbacks[label]:
                    assert call(owner) == receipt
            assert draws.call_count == 0 and owner.snapshot(label) == expected
    with AuthorityOwner.open(path / "recovered.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        recovered = owner.recover_campaign(recovery_id="entry", source=exported)
        assert owner.recover_campaign(recovery_id="entry", source=exported) == recovered
        assert owner.snapshot("node") == saved["node"]
        with patch.object(dice._rng, "randint") as draws:
            for call, receipt in callbacks["node"]:
                assert call(owner) == receipt
        assert draws.call_count == 0 and owner.snapshot("node") == saved["node"]
    result = {
        "entry": node,
        "mirrored": mirrored,
        "arms": arms,
        "negative_status": negative_status,
        "reopen_and_recovery_replay": True,
        "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "export": {
            "path": str(exported),
            "bytes": exported.stat().st_size,
            "sha256": hashlib.sha256(exported.read_bytes()).hexdigest(),
        },
        "qualification": (
            "Existing conformance inputs only, not canonical defaults. Real admitted GM tools, "
            "no lab overlays, provider or native acceptance. Owner-approved provisional entry "
            "values; not final balance."
        ),
    }
    (path / "proof.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def test_physical_baseline_guards_and_receipts_remain_real(owning_pair):
    proof = owning_pair
    assert proof["reopen_and_recovery_replay"]
    for stages in proof["arms"].values():
        start, fired, final = stages
        assert fired["weapon"]["magazine"]["rounds"] == start["weapon"]["magazine"]["rounds"] - 1
        assert fired["weapon"]["chamber"] == "LIVE"
        assert fired["ap"] < start["ap"]
        if proof["entry"] == "gunner":
            assert (
                final["weapon"]["magazine"]["rounds"] == start["weapon"]["magazine"]["rounds"] - 2
            )
            assert final["weapon"]["id"] == start["weapon"]["id"]
            assert final["weapon"]["current_recoil"] > fired["weapon"]["current_recoil"] > 0
        else:
            assert final["weapon"]["magazine"] == fired["weapon"]["spare_magazines"][-1]
            assert final["weapon"]["retained_magazines"][-1] == fired["weapon"]["magazine"]
            assert final["weapon"]["spare_magazines"] == fired["weapon"]["spare_magazines"][:-1]
            assert final["weapon"]["chamber"] == fired["weapon"]["chamber"]
            assert final["weapon"]["reload_ap"] == fired["weapon"]["reload_ap"]
            assert final["ap"] < fired["ap"]
    assert proof["negative_status"] in {"FIRE_REJECTED", "MISSING_PREREQUISITE"}


def test_authored_entry_changes_its_named_quantity_without_choosing_a_magnitude(owning_pair):
    """Retain the original TD34 relational regression at its owning boundary."""
    ordinary, specialized = owning_pair["arms"]["baseline"], owning_pair["arms"]["node"]
    if owning_pair["entry"] == "gunner":
        assert specialized[1]["weapon"]["current_recoil"] == ordinary[1]["weapon"]["current_recoil"]
        ordinary_gain = (
            ordinary[2]["weapon"]["current_recoil"] - ordinary[1]["weapon"]["current_recoil"]
        )
        specialized_gain = (
            specialized[2]["weapon"]["current_recoil"] - specialized[1]["weapon"]["current_recoil"]
        )
        assert specialized_gain < ordinary_gain, "GUNNER entry has no additional-recoil effect"
    else:
        ordinary_spend = ordinary[1]["ap"] - ordinary[2]["ap"]
        specialized_spend = specialized[1]["ap"] - specialized[2]["ap"]
        assert specialized_spend < ordinary_spend, "OPERATOR entry has no reload AP effect"
