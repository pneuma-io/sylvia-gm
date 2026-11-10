"""Suppression excludes ordinary AIM at the generated package/procedure boundary.

Use the package's unchanged conformance poses and parameters. Mirrored cases only
swap allegiance labels and the initial active side; no lab runtime transform is used.
"""

import hashlib
import json
import zipfile
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import MechanicContext, StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.package_library import manifest_from_record
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.td_source_fixture import candidate  # noqa: F401

NS = "pneuma-io.trigger-discipline"


class PackageProcedures:
    """Execute exact generated archive procedures through the host interpreter."""

    def __init__(self, archive, mirrored=False):
        with zipfile.ZipFile(archive) as package:
            record = next(
                row
                for row in json.loads(package.read("manifest.json"))["packages"]
                if row["id"] == NS
            )
            self.runtime = package.read(f"packages/{NS}/runtime/ruleset.yaml")
            self.state = json.loads(package.read(f"packages/{NS}/conformance/scenario.json"))[
                "initial_state"
            ]
        self.manifest = manifest_from_record(record)
        self.sequence = 0
        if mirrored:
            self.world["active_side"] = "hostiles"
            for actor in self.actors.values():
                actor["side"] = "hostiles" if actor["side"] == "allies" else "allies"

    @property
    def world(self):
        return self.state[NS + ".state"]

    @property
    def actors(self):
        return self.state[NS + ".entities"]

    def execute(self, tool, parameters):
        declaration = next(row for row in self.manifest.tools if row.name == tool)
        mechanic = materialize_package_mechanic(
            package_id=NS,
            package_version=self.manifest.version,
            package_sha256=hashlib.sha256(self.runtime).hexdigest(),
            declaration=declaration,
            runtime_bytes=self.runtime,
        )
        self.sequence += 1
        outcome = mechanic(
            MechanicContext(
                campaign_id="suppression-aim-procedures",
                turn_id="turn",
                action_id=f"step-{self.sequence}",
                state=self.state,
                parameters=parameters,
            )
        )
        for mutation in outcome.mutations:
            self.state[f"{mutation.namespace}.{mutation.key}"] = thaw_json_value(mutation.value)
        return outcome.result


def prepare(game, kind, aim):
    """Keep a real target-specific AIM solution through preparation and side transition."""
    if aim:
        game.execute(
            "aim",
            {
                "attacker": "vesper",
                "target": "hostile_alpha",
                "ap": 1,
                "aim_point": game.actors["hostile_alpha"]["center"],
            },
        )
        assert game.actors["vesper"]["aim"]["ap"] == 1
    before = deepcopy(game.actors["vesper"])
    game.execute(
        "establish_suppression" if kind == "suppression" else "prepare_overwatch",
        {
            "actor": "vesper",
            "zone_id": kind + "-lane",
            **({"rounds": 20} if kind == "suppression" else {}),
        },
    )
    assert game.world["prepared"][kind]["active"]
    assert game.actors["vesper"]["ap"] == before["ap"] - 2
    prepared = deepcopy(game.actors["vesper"])
    if kind == "suppression":
        assert (
            prepared["weapon"]["magazine"]["rounds"] == before["weapon"]["magazine"]["rounds"] - 20
        )
    else:
        assert prepared["weapon"] == before["weapon"]
    side = game.actors["vesper"]["side"]
    game.execute("end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
    assert game.actors["vesper"]["ap"] == 0
    assert game.actors["vesper"]["aim"]["ap"] == aim
    assert game.world["prepared"][kind]["active"]
    return prepared


def cross(game, order, face=14):
    with patch.object(dice._rng, "randint", return_value=face) as draw:
        result = game.execute(
            "resolve_trigger_crossing",
            {
                "actor": "hostile_alpha",
                "destination": [7, 0],
                "overwatcher": "vesper",
                "suppressor": "vesper",
                "first_effect": order,
                "overwatch_critical_effect": "DEEP_HIT",
                "suppression_critical_effect": "DEEP_HIT",
            },
        )
    return result, draw.call_count


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("order", ["SUPPRESSION", "OVERWATCH"])
@pytest.mark.parametrize("kind", ["suppression", "overwatch"])
def test_stored_aim_changes_only_ordinary_watch(candidate, kind, order, mirrored):  # noqa: F811
    offsets, records = [], []
    branch = "first" if order == kind.upper() else "second"
    for aim in (0, 1):
        game = PackageProcedures(candidate, mirrored)
        prepared = prepare(game, kind, aim)
        before = deepcopy(game.actors["vesper"])
        allowance = game.world["prepared"][kind].get("capacity")
        result, draws = cross(game, order)
        record = game.world["last_trigger"][kind]
        assert record["status"] == "RESOLVED"
        assert draws == 1
        prefix = f"_trigger_{kind}_{branch}"
        offsets.append(result["context"][prefix + "_aim_offset"])
        records.append(result["context"][prefix + "_actual_difficulty"])
        after = game.actors["vesper"]
        assert after["ap"] == before["ap"] == 0
        assert after["aim"]["ap"] == (aim if kind == "suppression" else 0)
        if kind == "suppression":
            assert after["weapon"] == prepared["weapon"] == before["weapon"]
            assert game.world["prepared"][kind]["capacity"] == allowance - 1
        else:
            assert (
                after["weapon"]["magazine"]["rounds"] == before["weapon"]["magazine"]["rounds"] - 1
            )
            assert after["weapon"]["current_recoil"] == before["weapon"]["current_recoil"] + max(
                1, before["weapon"]["recoil"]
            )
            assert game.world["prepared"][kind]["shots"] == 0
    if kind == "suppression":
        assert offsets == [0, 0]
        assert records[0] == records[1]
    else:
        assert offsets[0] == 0 and offsets[1] < 0
        assert records[1] == records[0] + offsets[1]


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("order", ["SUPPRESSION", "OVERWATCH"])
def test_depleted_zone_never_draws_or_spends_again(candidate, mirrored, order):  # noqa: F811
    game = PackageProcedures(candidate, mirrored)
    prepare(game, "suppression", 1)
    # Select the persisted depleted-state boundary without inventing an exposure action.
    game.world["prepared"]["suppression"].update(active=False, capacity=0)
    before = deepcopy(game.actors["vesper"])
    _, draws = cross(game, order)
    assert draws == 0
    assert "suppression" not in game.world["last_trigger"]
    assert game.actors["vesper"] == before


class OwnedProcedures:
    """Use the admitted campaign owner while retaining commands for exact-once proof."""

    def __init__(self, owner, campaign):
        self.owner = owner
        self.campaign = campaign
        self.commands = []
        self.receipts = []

    @property
    def state(self):
        return thaw_json_value(self.owner.snapshot(self.campaign).state)

    @property
    def world(self):
        return self.state[NS + ".state"]

    @property
    def actors(self):
        return self.state[NS + ".entities"]

    def execute(self, tool, parameters):
        before = self.owner.snapshot(self.campaign)
        identity = f"{self.campaign}-{len(self.commands)}"
        command = ActionCommand(
            campaign_id=self.campaign,
            action_id=identity,
            idempotency_key=identity,
            owner_id="aim-owner",
            writer=WriterRole.DM,
            expected_revision=before.revision,
            turn_id="turn",
            package_id=NS,
            tool_name=tool,
            parameters=parameters,
        )
        receipt = self.owner.execute_action(command)
        assert receipt.status.value == "committed"
        self.commands.append(command)
        self.receipts.append(receipt)
        after = self.owner.snapshot(self.campaign)
        assert after.revision == before.revision + 1
        assert self.owner.execute_action(command) == receipt
        assert self.owner.snapshot(self.campaign) == after
        return thaw_json_value(receipt.result)

    def replay(self, owner, expected):
        assert owner.snapshot(self.campaign) == expected
        for command, receipt in zip(self.commands, self.receipts, strict=True):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot(self.campaign) == expected


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("order", ["SUPPRESSION", "OVERWATCH"])
def test_admitted_aim_suppression_reopen_crossing_and_recovery(
    candidate, tmp_path, mirrored, order  # noqa: F811
):
    database = tmp_path / "authority.sqlite3"
    games, offsets, checkpoints, proof = [], [], [], []
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        for aim in (0, 1):
            scene = PackageProcedures(candidate, mirrored).state
            campaign = f"suppression-aim-{aim}"
            owner.create_campaign(
                CampaignCreate(
                    campaign_id=campaign,
                    display_name="Synthetic suppression AIM regression",
                    writer=WriterRole.DM,
                    package_lock=admitted.package_lock,
                    turn_id="turn",
                    initial_state=tuple(
                        StateValue(NS, key.removeprefix(NS + "."), value)
                        for key, value in scene.items()
                    ),
                )
            )
            game = OwnedProcedures(owner, campaign)
            prepare(game, "suppression", aim)
            games.append(game)
            checkpoints.append(owner.snapshot(campaign))
    # Reopen the ordinary saved preparation before any crossing is resolved.
    with AuthorityOwner.open(database, "aim-owner") as owner:
        for aim, game, saved in zip((0, 1), games, checkpoints, strict=True):
            game.owner = owner
            game.replay(owner, saved)
            assert game.actors["vesper"]["aim"]["ap"] == aim
            before = deepcopy(game.actors["vesper"])
            allowance = game.world["prepared"]["suppression"]["capacity"]
            result, draws = cross(game, order)
            assert draws == 1
            assert game.world["last_trigger"]["suppression"]["status"] == "RESOLVED"
            branch = "first" if order == "SUPPRESSION" else "second"
            offsets.append(result["context"][f"_trigger_suppression_{branch}_aim_offset"])
            assert game.actors["vesper"] == before
            assert game.world["prepared"]["suppression"]["capacity"] == allowance - 1
            saved = owner.snapshot(game.campaign)
            game.replay(owner, saved)
            export = tmp_path / f"aim-{aim}.sylvia-gm-campaign"
            owner.export_campaign(f"export-{aim}", game.campaign, export)
            with AuthorityOwner.open(
                tmp_path / f"recovered-{aim}.sqlite3", "aim-owner"
            ) as recovered:
                recovery = recovered.recover_campaign(recovery_id="recover-aim", source=export)
                assert (
                    recovered.recover_campaign(recovery_id="recover-aim", source=export) == recovery
                )
                game.replay(recovered, saved)
            with AuthorityOwner.open(
                tmp_path / f"recovered-{aim}.sqlite3", "aim-owner"
            ) as reopened:
                game.replay(reopened, saved)
            proof.append(
                {
                    "aim": aim,
                    "revision": saved.revision,
                    "offset": offsets[-1],
                    "shooter": game.actors["vesper"],
                    "trigger": game.world["last_trigger"],
                    "prepared": game.world["prepared"],
                    "state_sha256": hashlib.sha256(
                        json.dumps(
                            thaw_json_value(saved.state),
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                    "export_bytes": export.stat().st_size,
                    "export_sha256": hashlib.sha256(export.read_bytes()).hexdigest(),
                    "commands_replayed": len(game.commands),
                }
            )
        (tmp_path / "proof.json").write_text(
            json.dumps(
                {
                    "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "mirrored": mirrored,
                    "order": order,
                    "outcomes": proof,
                    "poses": "unchanged conformance scene; allegiance labels only swapped",
                    "qualification": "deterministic owner; no native/provider/balance acceptance",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        assert offsets == [0, 0]
