"""Exercise isolated admitted TD packages and save detached public cards, without inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

sys.dont_write_bytecode = True
NS = "pneuma-io.trigger-discipline"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--only", default="", help="Comma-separated operation names for focused follow-up cases."
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    temp = args.output / "temp"
    temp.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temp)
    sys.path[:0] = [str(args.source_root / "src"), str(args.source_root)]
    from build_td_result_completion import ACTIVITY, SPECS
    from tests.admitted_library_fixture import CANDIDATE_ID, prepare_library_database

    from sylvia_gm import dice
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from sylvia_gm.application.package_runtime import materialize_package_mechanic
    from sylvia_gm.domain.authority import (
        MechanicContext,
        Participant,
        ParticipantValidationStatus,
        StateValue,
        WriterRole,
        owned_payload,
        thaw_json_value,
    )
    from sylvia_gm.domain.package_library import ToolDeclaration

    def payload(archive):
        with zipfile.ZipFile(archive) as z:
            primary = next(
                v for v in json.loads(z.read("manifest.json"))["packages"] if v["id"] == NS
            )
            prefix = "packages/" + NS + "/"
            scenario = json.loads(z.read(prefix + "conformance/panel-edge-combat.json"))
            character = json.loads(z.read(prefix + "conformance/character.json"))
            runtime_bytes = z.read(prefix + "runtime/ruleset.yaml")
        return primary, scenario, character, runtime_bytes

    packages = {
        key: payload(path) for key, path in [("base", args.base), ("candidate", args.archive)]
    }

    def configured(name, variant):
        initial = deepcopy(packages["base"][1]["initial_state"])
        state, entities = initial[NS + ".state"], initial[NS + ".entities"]
        actor = "autodrone_alpha" if name == "autodrone_fire" else "hex"
        target = (
            "drone_target"
            if name in ("autodrone_fire", "fpv_fragmentation_strike")
            else "hostile_alpha"
        )
        parameters = {
            "actor": actor,
            "attacker": actor,
            "target": target,
            "recipient": "vesper",
            "intruder": "hex",
            "aim_point": entities[target]["center"],
            "ap": 1,
            "ap_cost": 1,
            "weapon_id": entities["hex"]["weapon"]["id"],
            "current_weapon_id": entities["hex"]["weapon"]["id"],
            "next_weapon_id": "reserve_carbine",
            "store_location": "STOWED",
            "location": "STOWED",
            "ground_id": "qa-ground",
            "zone_id": "suppression-lane" if name == "establish_suppression" else "overwatch-lane",
            "rounds": 5,
            "disposition": "RETAIN",
            "drone": "fpv_alpha",
            "landing_point": entities[target]["center"],
            "node_id": "lobby_terminal",
            "action": "GET_IN" if name == "intrude_network" else "CONNECT",
            "method": "INTRUDE",
            "route_step": ["lobby_terminal", "camera_gateway", "terminal_gateway"],
            "remote_function": "open_security_door",
            "amount": 3,
            "situation": "SECURED_WITH_TIME_AND_SUPPORT",
            "skill": "athletics",
            "aptitude": "force",
            "difficulty": 10,
            "modifier": 0,
            "maneuver": "TRIP",
            "held_item": "PRIMARY_WEAPON",
            "destination": [4, 3],
            "defender_id": "qa-defender",
            "response_id": "qa-response",
            "center": entities[target]["center"],
            "radius": 3,
            "start": entities["hex"]["center"],
            "end": [8.5, 3.5, 0.9],
        }
        entities["hex"]["reserve_weapons"] = [
            dict(deepcopy(entities["hex"]["weapon"]), id="reserve_carbine", location="STOWED")
        ]
        if name == "transfer_reserve_weapon":
            parameters["weapon_id"] = "reserve_carbine"
        if name == "cycle_weapon":
            entities["hex"]["weapon"].update(cycle_ap=1, needs_cycle=True, chamber="SPENT")
        if name == "recover_recoil":
            entities["hex"]["weapon"]["current_recoil"] = 4
        if name == "recover_between_encounters":
            entities["hex"].update(max_res=10, res=5)
        if name in ("resolve_cqc", "resolve_cqc_displacement"):
            entities[target].update(
                position_cell=[3, 3], center=[3.5, 3.5, 0.9], muzzle=[3.5, 3.5, 1.4]
            )
            for body in state["spatial_scene"]["entities"]:
                if body["id"] == target:
                    body["position"] = [3.5, 3.5, 0]
            if name == "resolve_cqc_displacement":
                parameters["maneuver"] = "SHOVE"
        if name in ("operate_remote_function",):
            parameters["node_id"] = "security_controller"
        if name == "pivot_network":
            parameters["node_id"] = "camera_gateway"
        if name == "end_hostiles_turn":
            state["active_side"] = "hostiles"
        if name == "resolve_blast_snapshot":
            # The authored area must remain inside the map bounds, including its height.
            parameters.update(center=[8, 8, 0], radius=1)
        if name == "autodrone_fire" and variant == "miss":
            # Persistent native recoil after prior fire makes this one shot a native miss.
            entities[actor]["weapon"]["current_recoil"] = 40
            entities[actor]["weapon"]["magazine"]["rounds"] = 10
        if name == "resolve_check" and variant == "failed_check":
            parameters["difficulty"] = 60
        if variant.startswith("inactive"):
            state["active_side"] = "hostiles" if entities[actor]["side"] == "allies" else "allies"
            # Poison the saved native result families. None may become a new rolled/hit result.
            for key in (
                "last_attack",
                "last_drone_attack",
                "last_check",
                "last_cqc",
                "last_network",
                "last_reload",
                "last_equipment",
                "last_trigger",
                "last_recovery",
                "last_defense",
            ):
                state[key] = {
                    "status": "STALE_SUCCESS_SENTINEL",
                    "outcome": "STALE_SUCCESS_SENTINEL",
                    "roll": 20,
                    "total": 999,
                    "success": True,
                    "cover_damage": 999,
                }
        if variant == "rejected":
            if name == "recover_between_encounters":
                parameters["situation"] = "UNSAFE_OR_INSUFFICIENT_TIME"
            elif name == "resolve_check":
                state["active_side"] = "hostiles"
            else:
                entities[actor]["ap"] = 0
                entities[actor]["active"] = False
        if variant.endswith("missing_ap"):
            del entities[actor]["ap"]
        return initial, parameters

    def execute_case(package_key, name, variant):
        archive = args.base if package_key == "base" else args.archive
        primary, scenario, character, _runtime = packages[package_key]
        case_dir = args.output / (name + "-" + variant) / package_key
        case_dir.mkdir(parents=True)
        db = case_dir / "campaign.sqlite3"
        prepare_library_database(db, archive.read_bytes())
        initial, parameters = configured(name, variant)
        with AuthorityOwner.open(db, "td-results-qa") as owner:
            artifact = primary["participant_artifacts"][0]
            owner.create_campaign(
                CampaignCreate(
                    campaign_id="td",
                    display_name="Synthetic TD result QA",
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
            tools = {t.name: t for t in owner.catalog("td")}

            def run(tool_name, values, identity):
                tool = tools[tool_name]
                parameters = {
                    k: v for k, v in values.items() if k in tool.input_schema["properties"]
                }
                command = ActionCommand(
                    campaign_id="td",
                    action_id=identity,
                    idempotency_key=identity,
                    owner_id="td-results-qa",
                    writer=WriterRole.DM,
                    expected_revision=owner.snapshot("td").revision,
                    turn_id="turn",
                    package_id=NS,
                    tool_name=tool_name,
                    parameters=parameters,
                )
                receipt = owner.execute_action(command)
                assert str(receipt.status) == "committed", (
                    tool_name,
                    thaw_json_value(receipt.result),
                )
                return receipt, command

            if name == "pickup_weapon" and variant == "ordinary":
                run("drop_weapon", parameters, "setup-drop")
                current = owner.snapshot("td")
                parameters.update(
                    ground_id=next(iter(current.state[NS + ".state"]["ground_objects"])),
                    location="READY",
                )
            before = owner.snapshot("td")
            dice.set_seed(1524)
            receipt, command = run(name, parameters, "tested-operation")
            after = owner.snapshot("td")
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("td") == after
            value = thaw_json_value(receipt.result)
            card = value.get("result_card")
            if package_key == "candidate":
                if name in ACTIVITY:
                    assert card is None and value.get("activity_label") == ACTIVITY[name], value
                else:
                    assert isinstance(card, dict) and card.get("summary"), (name, variant, value)
                    rows = {row["label"]: row["value"] for row in card["summary"] + card["details"]}
                    if variant.endswith("missing_ap"):
                        assert "ap" not in before.state[NS + ".entities"][parameters["actor"]]
                        assert "AP spent" not in rows and "AP remaining" not in rows, rows
                    if "AP spent" in rows:
                        actor = parameters["actor"]
                        assert (
                            rows["AP spent"]
                            == before.state[NS + ".entities"][actor]["ap"]
                            - after.state[NS + ".entities"][actor]["ap"]
                        )
                    if name in ("autodrone_fire", "establish_suppression") and variant in (
                        "ordinary",
                        "miss",
                    ):
                        actor = parameters["actor"]
                        before_rounds = before.state[NS + ".entities"][actor]["weapon"]["magazine"][
                            "rounds"
                        ]
                        after_rounds = after.state[NS + ".entities"][actor]["weapon"]["magazine"][
                            "rounds"
                        ]
                        assert rows["Magazine rounds spent"] == before_rounds - after_rounds
                        assert rows["Magazine rounds after"] == after_rounds
                    if name == "autodrone_fire" and variant == "miss":
                        assert rows["Attack outcome"] == "MISS"
                        target = parameters["target"]
                        for field in ("hp", "res"):
                            assert (
                                before.state[NS + ".entities"][target][field]
                                == after.state[NS + ".entities"][target][field]
                            )
                        assert "HP damage applied" not in rows and "RES damage applied" not in rows
                    text = json.dumps(card)
                    assert "_card_" not in text, (name, variant, card)
                    if name == "resolve_check" and not variant.startswith("inactive"):
                        success = after.state[NS + ".state"]["last_check"]["success"]
                        assert rows["Succeeded"] is success
                        assert rows["Result"] == ("PASSED" if success else "FAILED")
                        assert success is (rows["Total"] >= rows["Difficulty"])
                    assert "STALE_SUCCESS_SENTINEL" not in text and "999" not in text, (
                        name,
                        variant,
                        card,
                    )
                    for private in (
                        "credential_id",
                        "network_topology",
                        "network_credentials",
                        "spatial_snapshot",
                        "snapshot_id",
                        "_crossings",
                    ):
                        assert private not in text, (name, private)
                    if variant.startswith("inactive") and name not in (
                        "recover_between_encounters",
                        "end_allies_turn",
                        "end_hostiles_turn",
                    ):
                        assert any(
                            str(row["value"]).startswith("INACTIVE_SIDE:")
                            for row in card["summary"]
                        ), card
                        assert not card.get("equation"), card
                        assert not any(
                            row["label"] in ("D20", "Total", "Succeeded") for row in card["details"]
                        ), card
                    (case_dir / "public-card.json").write_text(
                        json.dumps(card, indent=2) + "\n", encoding="utf-8"
                    )
                # Save public presentation and scalar deltas; keep spatial/network state private.
                summary = {
                    "operation": name,
                    "variant": variant,
                    "card": card,
                    "activity_label": value.get("activity_label"),
                    "native_status": value.get("status"),
                    "idempotent_replay": True,
                    "ap_before": before.state[NS + ".entities"]
                    .get(parameters.get("actor", "hex"), {})
                    .get("ap"),
                    "ap_after": after.state[NS + ".entities"]
                    .get(parameters.get("actor", "hex"), {})
                    .get("ap"),
                }
            else:
                summary = {}
        with AuthorityOwner.open(db, "td-results-reopen") as owner:
            assert owner.snapshot("td") == after
            assert (
                thaw_json_value(owner.snapshot("td").actions[-1].result).get("result_card") == card
            )
        summary["reopen_exact"] = True
        return (
            thaw_json_value(after.state),
            summary,
            {
                "state": thaw_json_value(before.state),
                "parameters": thaw_json_value(command.parameters),
            },
        )

    mechanic_cache = {}

    def differential(name, values):
        outcomes = []
        for package_key in ("base", "candidate"):
            key = (package_key, name)
            if key not in mechanic_cache:
                record = next(t for t in packages[package_key][0]["tools"] if t["name"] == name)
                declaration = ToolDeclaration(
                    name=record["name"],
                    capability=record["capability"],
                    input_schema=owned_payload(record["input_schema"]),
                    state_predicates=(),
                    turn_statuses=tuple(record["turn_statuses"]),
                    writers=tuple(record["writers"]),
                )
                mechanic_cache[key] = materialize_package_mechanic(
                    package_id=NS,
                    package_version="comparison",
                    package_sha256="a" * 64,
                    declaration=declaration,
                    runtime_bytes=packages[package_key][3],
                )
            outcomes.append(
                mechanic_cache[key](
                    MechanicContext(
                        campaign_id="same-identity-differential",
                        turn_id="turn",
                        action_id="same-native-seed",
                        state=owned_payload(values["state"]),
                        parameters=owned_payload(values["parameters"]),
                        participants=owned_payload({}),
                    )
                )
            )
        before, after = outcomes
        assert before.mutations == after.mutations, (
            name,
            "Persistent native mutations differ under identical seed/input",
        )
        assert before.participant_mutations == after.participant_mutations
        assert before.consequences == after.consequences and before.choice == after.choice

        def native_result(value):
            result = thaw_json_value(value.result)
            result.pop("result_card", None)
            result.pop("activity_label", None)
            result["context"] = {
                k: v for k, v in result.get("context", {}).items() if not k.startswith("_card_")
            }
            return result

        assert native_result(before) == native_result(after), (
            name,
            "Native result context differs",
        )
        return {
            "same_identity_native_mutations_exact": True,
            "same_identity_native_result_exact": True,
        }

    proof = {
        "provider_inference": False,
        "physical_input": "not run",
        "cases": [],
        "failures": [],
        "base_sha256": hashlib.sha256(args.base.read_bytes()).hexdigest(),
        "candidate_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
    }
    selected = [
        (n, v)
        for n in SPECS
        for v in ("ordinary", "inactive")
        if v == "ordinary"
        or n not in ("end_allies_turn", "end_hostiles_turn", "recover_between_encounters")
    ]
    selected += [("recover_between_encounters", "rejected")]
    selected += [
        ("aim", "rejected"),
        ("autodrone_fire", "rejected"),
        ("autodrone_fire", "miss"),
        ("fpv_fragmentation_strike", "rejected"),
        ("resolve_check", "failed_check"),
    ]
    selected += [(n, "ordinary") for n in ACTIVITY]
    selected += [
        ("aim", "inactive_missing_ap"),
        ("resolve_check", "inactive_missing_ap"),
        ("resolve_check", "ordinary_missing_ap"),
        ("recover_between_encounters", "ordinary_missing_ap"),
    ]
    if args.only:
        selected = [case for case in selected if case[0] in set(args.only.split(","))]
    print(json.dumps({"owned_pid": os.getpid()}), flush=True)
    for name, variant in selected:
        try:
            baseline, _, values = execute_case("base", name, variant)
            candidate, result, _ = execute_case("candidate", name, variant)
            result.update(differential(name, values))
            # Owner seeding deliberately includes package version/hash; separate admitted
            # archives can roll differently. The differential above fixes identity/seed.
            result["admitted_owner_native_state_matches_base"] = baseline == candidate
            if variant.startswith("inactive"):
                assert baseline == candidate, (name, variant, "Inactive state changed")
            proof["cases"].append(result)
            print(json.dumps({"passed": name, "variant": variant}), flush=True)
        except Exception as error:
            failure = {
                "operation": name,
                "variant": variant,
                "type": type(error).__name__,
                "message": str(error)[:1400],
                "code": getattr(error, "code", None),
                "field_paths": getattr(error, "field_paths", ()),
            }
            proof["failures"].append(failure)
            print(json.dumps({"failed": failure}), flush=True)
        (args.output / "proof.json").write_text(
            json.dumps(proof, indent=2) + "\n", encoding="utf-8"
        )
    proof["passed"] = not proof["failures"]
    (args.output / "proof.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": proof["passed"],
                "cases": len(proof["cases"]),
                "failures": len(proof["failures"]),
            }
        ),
        flush=True,
    )
    if proof["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
