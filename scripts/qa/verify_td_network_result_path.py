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
    initial = scenario["initial_state"]
    initial[NS + ".state"]["network_defenders"] = {
        "qa": {
            "active": True,
            "operator": "hostile_alpha",
            "awareness": {"hex": {"level": 1}},
            "responses": {
                "end_session": {
                    "mode": "ACTOR",
                    "ap": 1,
                    "min_awareness": 1,
                    "kind": "END_SESSION",
                    "target": "lobby_terminal",
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

            def state():
                return owner.snapshot("network-result-qa")

            def run(name, values):
                nonlocal counter
                counter += 1
                before = state()
                identity = f"network-step-{counter}-{name}"
                receipt = owner.execute_action(
                    ActionCommand(
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
                )
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
            # This authored actor has no ROUTE PERSISTENCE specialty and no alternate
            # route after link loss. Its rejection must remain a rejection card.
            assert (
                run(
                    "preserve_network_route",
                    {
                        "actor": "hex",
                        "node_id": "security_controller",
                        "method": "INTRUDE",
                        "route_step": ["camera_gateway", "security_controller", "gateway_security"],
                    },
                )[0]
                == "NO_SURVIVING_SESSION_OR_ROUTE"
            )
            proof["route_persistence_without_required_setup_rejected"] = True
            run("end_allies_turn", {})
            assert (
                run(
                    "respond_network_defense",
                    {
                        "actor": "hostile_alpha",
                        "intruder": "hex",
                        "defender_id": "qa",
                        "response_id": "end_session",
                    },
                )[0]
                == "RESPONDED"
            )
            session = state().state[NS + ".state"]["network_sessions"]["hex"]["nodes"][
                "lobby_terminal"
            ]
            assert session["active"] is False
            saved = state()
            proof.update(door_opened=True, defender_ended_actual_session=True)
        with AuthorityOwner.open(db, "td-network-results-reopen") as owner:
            assert owner.snapshot("network-result-qa") == saved
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
