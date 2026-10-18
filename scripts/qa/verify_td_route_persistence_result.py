"""Actual authored TD network progression with public saved result cards; no inference."""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
NS = "pneuma-io.trigger-discipline"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    temp = args.output / "temp"
    temp.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temp)
    sys.path[:0] = [str(args.source_root / "src"), str(args.source_root)]
    from tests.admitted_library_fixture import CANDIDATE_ID, prepare_library_database

    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from sylvia_gm.domain.authority import (
        Participant,
        ParticipantValidationStatus,
        StateValue,
        WriterRole,
        thaw_json_value,
    )

    with zipfile.ZipFile(args.archive) as z:
        primary = next(v for v in json.loads(z.read("manifest.json"))["packages"] if v["id"] == NS)
        scenario = json.loads(z.read("packages/" + NS + "/conformance/panel-edge-combat.json"))
        character = json.loads(z.read("packages/" + NS + "/conformance/character.json"))
    print(json.dumps({"owned_pid": os.getpid()}), flush=True)
    initial = scenario["initial_state"]
    # Explicit synthetic prerequisites, authored before campaign creation only.
    # The actual sessions and lost link below are produced by public commands.
    initial[NS + ".entities"]["hex"]["specialties"].append(
        "specialty.networker.tier_3.route_persistence"
    )
    initial[NS + ".state"]["network_intrusions"]["security_controller"]["reentry"][
        "ROUTE_PERSISTENCE"
    ] = {"ap": 1}
    initial[NS + ".state"]["network"]["links"]["qa-alternate-security"] = {
        "direction": "bidirectional",
        "kind": "ethernet",
        "label": "Known backup uplink",
        "source": "lobby_terminal",
        "target": "security_controller",
        "status": "online",
        "visible": True,
    }
    initial[NS + ".state"]["network_topology"]["links"]["qa-alternate-security"] = {
        "generation": 1,
        "online": True,
    }
    initial[NS + ".state"]["network_defenders"] = {
        "qa": {
            "active": True,
            "operator": "hostile_alpha",
            "awareness": {"hex": {"level": 1}},
            "responses": {
                "isolate_link": {
                    "mode": "ACTOR",
                    "ap": 1,
                    "min_awareness": 1,
                    "kind": "ISOLATE_LINK",
                    "target": "gateway_security",
                }
            },
        }
    }
    db = args.output / "campaign.sqlite3"
    prepare_library_database(db, args.archive.read_bytes())
    proof = {
        "archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "provider_inference": False,
        "physical_input": "not run",
        "steps": [],
    }
    try:
        with AuthorityOwner.open(db, "td-network-results") as owner:
            artifact = primary["participant_artifacts"][0]
            owner.create_campaign(
                CampaignCreate(
                    campaign_id="network-result-qa",
                    display_name="Synthetic native network result progression",
                    writer=WriterRole.DM,
                    package_lock=owner.package_candidate(CANDIDATE_ID).package_lock,
                    turn_id="turn",
                    participants=(
                        Participant(
                            participant_id="hex",
                            kind=artifact["kind"],
                            package_id=NS,
                            package_version=primary["version"],
                            package_sha256=primary["content_sha256"],
                            schema_id=artifact["schema_id"],
                            schema_sha256=artifact["schema_sha256"],
                            validation_status=ParticipantValidationStatus.VALIDATED,
                            data=character["artifact"],
                        ),
                    ),
                    initial_state=tuple(
                        StateValue(NS, k.removeprefix(NS + "."), v) for k, v in initial.items()
                    ),
                )
            )
            counter = 0
            commands = []
            receipts = []

            def state():
                return owner.snapshot("network-result-qa")

            def run(name, values):
                nonlocal counter
                counter += 1
                before = state()
                identity = f"network-step-{counter}-{name}"
                command = ActionCommand(
                    campaign_id="network-result-qa",
                    action_id=identity,
                    idempotency_key=identity,
                    owner_id="td-network-results",
                    writer=WriterRole.DM,
                    expected_revision=before.revision,
                    turn_id="turn",
                    package_id=NS,
                    tool_name=name,
                    parameters=values,
                )
                receipt = owner.execute_action(command)
                after = state()
                assert owner.execute_action(command) == receipt
                assert state() == after
                commands.append(command)
                receipts.append(receipt)
                assert str(receipt.status) == "committed", (name, thaw_json_value(receipt.result))
                card = thaw_json_value(receipt.result).get("result_card")
                assert card and card["summary"], name
                rows = {row["label"]: row["value"] for row in card["summary"] + card["details"]}
                status = rows["Result"]
                if "AP spent" in rows:
                    actor = values["actor"]
                    assert (
                        rows["AP spent"]
                        == before.state[NS + ".entities"][actor]["ap"]
                        - state().state[NS + ".entities"][actor]["ap"]
                    )
                if card.get("equation"):
                    equation = card["equation"]
                    assert equation["roll"] + equation["modifier"] == equation["total"]
                text = json.dumps(card)
                for private in (
                    "credential_id",
                    "network_topology",
                    "network_credentials",
                    "snapshot_id",
                    "awareness",
                ):
                    assert private not in text
                step = {"operation": name, "status": status, "card": card}
                proof["steps"].append(step)
                (args.output / f"card-{counter:02d}-{name}.json").write_text(
                    json.dumps(card, indent=2) + "\n", encoding="utf-8"
                )
                (args.output / "proof.json").write_text(
                    json.dumps(proof, indent=2) + "\n", encoding="utf-8"
                )
                print(
                    json.dumps({"step": counter, "operation": name, "status": status}), flush=True
                )
                return status, rows

            def refresh():
                current = state().state[NS + ".state"]["active_side"]
                if current == "allies":
                    run("end_allies_turn", {})
                run("end_hostiles_turn", {})
                assert state().state[NS + ".state"]["active_side"] == "allies"

            def check(name, values, cost):
                while True:
                    if state().state[NS + ".entities"]["hex"]["ap"] < cost:
                        refresh()
                    status, rows = run(name, values)
                    if status == "COMMITTED":
                        assert rows.get("Succeeded") is True
                        return
                    assert status == "FAILED_CHECK", (name, status)
                    assert rows["AP spent"] > 0
                    refresh()  # Paid failed checks remain recorded; try on a genuine new turn.

            assert (
                run(
                    "network_connection",
                    {"actor": "hex", "node_id": "lobby_terminal", "action": "CONNECT"},
                )[0]
                == "CONNECT"
            )
            check(
                "intrude_network",
                {
                    "actor": "hex",
                    "node_id": "lobby_terminal",
                    "method": "INTRUDE",
                    "action": "GET_IN",
                },
                2,
            )
            check(
                "intrude_network",
                {
                    "actor": "hex",
                    "node_id": "lobby_terminal",
                    "method": "INTRUDE",
                    "action": "DISCOVER",
                },
                2,
            )
            check(
                "pivot_network",
                {
                    "actor": "hex",
                    "node_id": "camera_gateway",
                    "method": "INTRUDE",
                    "route_step": ["lobby_terminal", "camera_gateway", "terminal_gateway"],
                },
                2,
            )
            check(
                "intrude_network",
                {
                    "actor": "hex",
                    "node_id": "camera_gateway",
                    "method": "INTRUDE",
                    "action": "DISCOVER",
                },
                2,
            )
            check(
                "pivot_network",
                {
                    "actor": "hex",
                    "node_id": "security_controller",
                    "method": "INTRUDE",
                    "route_step": ["camera_gateway", "security_controller", "gateway_security"],
                },
                3,
            )
            check(
                "intrude_network",
                {
                    "actor": "hex",
                    "node_id": "security_controller",
                    "method": "INTRUDE",
                    "action": "ESCALATE",
                },
                3,
            )
            if state().state[NS + ".entities"]["hex"]["ap"] < 1:
                refresh()
            assert (
                run(
                    "operate_remote_function",
                    {
                        "actor": "hex",
                        "node_id": "security_controller",
                        "remote_function": "open_security_door",
                    },
                )[0]
                == "OPERATED"
            )
            door = next(
                v
                for v in state().state[NS + ".state"]["spatial_scene"]["geometry"]
                if v["id"] == "security-door"
            )
            assert (
                door["active"] is False
                and door["movement"] == "passable"
                and door["visibility"] == "clear"
            )
            route = {
                "actor": "hex",
                "node_id": "security_controller",
                "method": "INTRUDE",
                "route_step": ["lobby_terminal", "security_controller", "qa-alternate-security"],
            }
            # All prerequisites except a lost link are present. Zero losses must reject.
            no_loss_before = state()
            assert run("preserve_network_route", route)[0] == "NO_SURVIVING_SESSION_OR_ROUTE"
            assert (
                state().state[NS + ".entities"]["hex"]["ap"]
                == (no_loss_before.state[NS + ".entities"]["hex"]["ap"])
            )
            run("end_allies_turn", {})
            assert (
                run(
                    "respond_network_defense",
                    {
                        "actor": "hostile_alpha",
                        "intruder": "hex",
                        "defender_id": "qa",
                        "response_id": "isolate_link",
                    },
                )[0]
                == "RESPONDED"
            )
            damaged = state().state[NS + ".state"]
            assert damaged["network_topology"]["links"]["gateway_security"]["online"] is False
            old_sessions = thaw_json_value(damaged["network_sessions"]["hex"]["nodes"])
            old_target = old_sessions["security_controller"]
            assert old_target["active"] is True
            assert len(old_target["links"]) == 2
            lost = [
                link
                for i, link in enumerate(old_target["links"])
                if (
                    not damaged["network_topology"]["links"][link]["online"]
                    or damaged["network_topology"]["links"][link]["generation"]
                    != old_target["link_generations"][i]
                )
            ]
            assert lost == ["gateway_security"]
            run("end_hostiles_turn", {})
            before_reroute = state()
            status, rows = run("preserve_network_route", route)
            assert status == "REROUTED" and rows["Succeeded"] is True
            assert rows["AP spent"] == 1 and not proof["steps"][-1]["card"].get("equation")
            reroute_index = len(receipts) - 1
            after_reroute = state()
            new_sessions = thaw_json_value(
                after_reroute.state[NS + ".state"]["network_sessions"]["hex"]["nodes"]
            )
            expected_target = dict(
                old_target,
                nodes=["lobby_terminal", "security_controller"],
                links=["qa-alternate-security"],
                node_generations=[1, 1],
                link_generations=[1],
                session_generations=[
                    old_sessions["lobby_terminal"]["generation"],
                    old_target["generation"],
                ],
            )
            assert new_sessions["security_controller"] == expected_target
            assert {k: v for k, v in new_sessions.items() if k != "security_controller"} == {
                k: v for k, v in old_sessions.items() if k != "security_controller"
            }
            assert after_reroute.state[NS + ".entities"]["hex"]["ap"] == (
                before_reroute.state[NS + ".entities"]["hex"]["ap"] - 1
            )
            assert after_reroute.state[NS + ".state"]["network"]["revision"] == (
                before_reroute.state[NS + ".state"]["network"]["revision"] + 1
            )
            # An already repaired route has zero lost links: ordinary repeat rejects.
            assert run("preserve_network_route", route)[0] == "NO_SURVIVING_SESSION_OR_ROUTE"
            assert (
                state().state[NS + ".entities"]["hex"]["ap"]
                == (after_reroute.state[NS + ".entities"]["hex"]["ap"])
            )
            assert (
                state().state[NS + ".state"]["network_sessions"]
                == (after_reroute.state[NS + ".state"]["network_sessions"])
            )
            saved = state()
            saved_card = thaw_json_value(receipts[reroute_index].result)["result_card"]
            proof.update(
                door_opened=True,
                actual_public_link_isolated=True,
                exact_one_link_loss=True,
                surviving_session_rerouted=True,
                target_privilege_generation_preserved=True,
                other_sessions_exact=True,
                explicit_one_ap_debited=True,
                zero_loss_and_repeated_reroute_rejected=True,
                idempotent_replay_every_step=True,
                prerequisite_fixture=(
                    "Synthetic specialty, authored AP1 reentry, known alternate link; "
                    "all sessions and loss created by ordinary public commands."
                ),
            )
        with AuthorityOwner.open(db, "td-network-results") as owner:
            assert owner.snapshot("network-result-qa") == saved
            assert owner.execute_action(commands[reroute_index]) == receipts[reroute_index]
            assert owner.snapshot("network-result-qa") == saved
            restored = owner.snapshot("network-result-qa").actions[reroute_index]
            assert thaw_json_value(restored.result)["result_card"] == saved_card
        proof.update(passed=True, reopen_exact=True)
    except Exception as error:
        proof.update(
            passed=False, failure={"type": type(error).__name__, "message": str(error)[:2000]}
        )
        raise
    finally:
        (args.output / "proof.json").write_text(
            json.dumps(proof, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "passed": proof["passed"],
                "steps": len(proof["steps"]),
                "reopen_exact": proof["reopen_exact"],
            }
        )
    )


if __name__ == "__main__":
    main()
