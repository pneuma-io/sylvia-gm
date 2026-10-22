from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import tempfile
from typing import Any

from .builder import CONFORMANCE_SETTING_ID, PACKAGE_ID, publish_candidate


CAMPAIGN_ID = "trigger-discipline-conformance"
TRIGGER_CAMPAIGN_ID = "trigger-discipline-trigger-reopen"
ACTIVATION_CAMPAIGN_ID = "trigger-discipline-activation-probe"
TURN_ID = "trigger-discipline-turn"
OWNER_ID = "trigger-discipline-qualification-owner"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_host(sylvia_root: Path) -> dict[str, Any]:
    source_path = str(sylvia_root.resolve() / "src")
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from sylvia_gm.domain.authority import (
        PackageLock,
        Participant,
        ParticipantValidationStatus,
        StateValue,
        WriterRole,
        thaw_json_value,
    )
    from sylvia_gm.domain.package_library import ActivationCommand
    from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog

    return {
        "ActionCommand": ActionCommand,
        "ActivationCommand": ActivationCommand,
        "AuthorityOwner": AuthorityOwner,
        "CampaignCreate": CampaignCreate,
        "Mechanics": StatelessMcpMechanics,
        "PackageLock": PackageLock,
        "Participant": Participant,
        "ParticipantValidationStatus": ParticipantValidationStatus,
        "StateValue": StateValue,
        "WriterRole": WriterRole,
        "thaw": thaw_json_value,
        "campaign_creation_catalog": _campaign_creation_catalog,
    }


def qualify_candidate(workspace: Path, sylvia_root: Path) -> dict[str, Any]:
    """Run the real Format-3 install/campaign/reopen/replay journey."""

    root = workspace.resolve()
    host = _load_host(sylvia_root)
    first = publish_candidate(root, sylvia_root)
    artifact = root / first["artifact"]
    first_bytes = artifact.read_bytes()
    second = publish_candidate(root, sylvia_root)
    second_bytes = (root / second["artifact"]).read_bytes()
    if first != second or first_bytes != second_bytes:
        raise AssertionError("repeated package publication was not byte-identical")

    scenario = json.loads(
        (root / "ruleset-draft" / "files" / "conformance" / "scenario.json").read_text(
            encoding="utf-8"
        )
    )
    initial = scenario["initial_state"]
    character = json.loads(
        (root / "ruleset-draft" / "files" / "conformance" / "character.json").read_text(
            encoding="utf-8"
        )
    )["artifact"]
    descriptor = json.loads((root / "ruleset-draft" / "package.json").read_text(encoding="utf-8"))
    character_declaration = descriptor["participant_artifacts"][0]
    character_schema_sha256 = _sha256(
        json.dumps(
            character_declaration["schema"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    StateValue = host["StateValue"]
    WriterRole = host["WriterRole"]
    action_results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="trigger-discipline-qualification-") as temp:
        database = Path(temp) / "authority.sqlite3"
        with host["AuthorityOwner"].open(database, OWNER_ID) as owner:
            candidate = owner.install_package_archive(
                io.BytesIO(first_bytes), install_id="trigger-discipline-candidate"
            )
            creation_catalog = host["campaign_creation_catalog"](Path(temp), owner)
            creation_ruleset = next(
                (
                    item
                    for item in creation_catalog
                    if item.get("kind") == "ruleset" and item.get("id") == PACKAGE_ID
                ),
                None,
            )
            if creation_ruleset is None:
                raise AssertionError("installed ruleset was absent from campaign creation")
            if [item["id"] for item in creation_ruleset["compatible_settings"]] != [
                CONFORMANCE_SETTING_ID
            ]:
                raise AssertionError(
                    "campaign creation did not expose the exact conformance setting"
                )
            owner.create_campaign(
                host["CampaignCreate"](
                    campaign_id=ACTIVATION_CAMPAIGN_ID,
                    display_name="Trigger Discipline activation probe",
                    writer=WriterRole.DM,
                    package_lock=host["PackageLock"](entries=()),
                    turn_id="trigger-discipline-activation-turn",
                    initial_state=tuple(
                        StateValue(PACKAGE_ID, qualified.rsplit(".", 1)[1], {})
                        for qualified in sorted(initial)
                    ),
                )
            )
            owner.complete_turn(
                ACTIVATION_CAMPAIGN_ID, "trigger-discipline-activation-turn"
            )
            activation_lock = owner.current_package_lock(ACTIVATION_CAMPAIGN_ID)
            activation = owner.activate_package(
                host["ActivationCommand"](
                    campaign_id=ACTIVATION_CAMPAIGN_ID,
                    candidate_id=candidate.candidate_id,
                    activation_id="trigger-discipline-explicit-activation",
                    expected_revision=0,
                    expected_lock_id=activation_lock.lock_id,
                    migration_id="trigger-discipline-initial-state-v0-1",
                ),
                lambda _state: initial,
            )
            if activation.status.value != "committed":
                raise AssertionError("explicit package activation did not commit")
            package_entry = next(
                entry
                for entry in candidate.package_lock.entries
                if entry.package_id == PACKAGE_ID
            )
            invalid_character = deepcopy(character)
            del invalid_character["skills"]["marksmanship"]
            try:
                owner.create_campaign(
                    host["CampaignCreate"](
                        campaign_id="trigger-discipline-invalid-character-probe",
                        display_name="Trigger Discipline invalid character probe",
                        writer=WriterRole.DM,
                        package_lock=candidate.package_lock,
                        turn_id="trigger-discipline-invalid-character-turn",
                        participants=(
                            host["Participant"](
                                "invalid-hex",
                                "trigger-discipline.character",
                                package_entry.package_id,
                                package_entry.version,
                                invalid_character,
                                package_sha256=package_entry.sha256,
                                schema_id=character_declaration["schema_id"],
                                schema_sha256=character_schema_sha256,
                                validation_status=host["ParticipantValidationStatus"].VALIDATED,
                            ),
                        ),
                    )
                )
            except Exception:
                invalid_character_rejected = True
            else:
                invalid_character_rejected = False
            if not invalid_character_rejected:
                raise AssertionError("participant schema accepted a runtime-incomplete skill object")

            owner.create_campaign(
                host["CampaignCreate"](
                    campaign_id=CAMPAIGN_ID,
                    display_name="Trigger Discipline conformance mission",
                    writer=WriterRole.DM,
                    package_lock=candidate.package_lock,
                    turn_id=TURN_ID,
                    participants=(
                        host["Participant"](
                            "hex",
                            "trigger-discipline.character",
                            package_entry.package_id,
                            package_entry.version,
                            character,
                            package_sha256=package_entry.sha256,
                            schema_id=character_declaration["schema_id"],
                            schema_sha256=character_schema_sha256,
                            validation_status=host["ParticipantValidationStatus"].VALIDATED,
                        ),
                    ),
                    initial_state=tuple(
                        StateValue(PACKAGE_ID, qualified.rsplit(".", 1)[1], value)
                        for qualified, value in sorted(initial.items())
                    ),
                )
            )
            owner.create_campaign(
                host["CampaignCreate"](
                    campaign_id=TRIGGER_CAMPAIGN_ID,
                    display_name="Trigger Discipline pending trigger reopen",
                    writer=WriterRole.DM,
                    package_lock=candidate.package_lock,
                    turn_id=f"{TURN_ID}-triggers",
                    initial_state=tuple(
                        StateValue(PACKAGE_ID, qualified.rsplit(".", 1)[1], value)
                        for qualified, value in sorted(initial.items())
                    ),
                )
            )
            mechanics = host["Mechanics"](owner)
            trigger_action_results: list[dict[str, Any]] = []
            action_commands: dict[str, Any] = {}

            def execute_for(
                campaign_id: str,
                turn_id: str,
                suffix: str,
                tool: str,
                parameters: dict[str, object],
                sink: list[dict[str, Any]],
            ):
                revision = owner.snapshot(campaign_id).revision
                command = host["ActionCommand"](
                    campaign_id=campaign_id,
                    action_id=f"td-{suffix}",
                    idempotency_key=f"td-idempotency-{suffix}",
                    owner_id=OWNER_ID,
                    writer=WriterRole.DM,
                    expected_revision=revision,
                    turn_id=turn_id,
                    package_id=PACKAGE_ID,
                    tool_name=tool,
                    parameters=parameters,
                )
                result = mechanics.execute_action(command)
                action_commands[command.action_id] = command
                sink.append(
                    {
                        "tool": tool,
                        "action_id": command.action_id,
                        "revision": result.resulting_revision,
                        "status": result.status.value,
                        "result": host["thaw"](result.result),
                        "result_sha256": _sha256(
                            json.dumps(
                                host["thaw"](result.result),
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ),
                    }
                )
                return command, result

            def execute(suffix: str, tool: str, parameters: dict[str, object]):
                return execute_for(
                    CAMPAIGN_ID,
                    TURN_ID,
                    suffix,
                    tool,
                    parameters,
                    action_results,
                )

            def execute_trigger(
                suffix: str, tool: str, parameters: dict[str, object]
            ):
                return execute_for(
                    TRIGGER_CAMPAIGN_ID,
                    f"{TURN_ID}-triggers",
                    f"trigger-{suffix}",
                    tool,
                    parameters,
                    trigger_action_results,
                )

            def network_state() -> dict[str, Any]:
                return host["thaw"](owner.snapshot(CAMPAIGN_ID).state)[
                    f"{PACKAGE_ID}.state"
                ]

            def package_state() -> tuple[dict[str, Any], dict[str, Any]]:
                current = host["thaw"](owner.snapshot(CAMPAIGN_ID).state)
                return (
                    current[f"{PACKAGE_ID}.state"],
                    current[f"{PACKAGE_ID}.entities"],
                )

            def scene_state() -> dict[str, Any]:
                return package_state()[0]["spatial_scene"]

            def d20_suffix(
                *,
                prefix: str,
                tool: str,
                parameters: dict[str, object],
                desired_roll: int,
            ) -> str:
                rules, _entities = package_state()
                runtime_parameters = deepcopy(parameters)
                runtime_parameters["active_side"] = rules["active_side"]
                if tool in {"fire_single", "fire_burst", "fire_full_auto"}:
                    runtime_parameters["security_door_open"] = rules["physical"][
                        "security_door_open"
                    ]
                for candidate in range(1, 10_001):
                    suffix = f"{prefix}-seed-{candidate}"
                    seed_basis = json.dumps(
                        {
                            "campaign_id": CAMPAIGN_ID,
                            "turn_id": TURN_ID,
                            "action_id": f"td-{suffix}",
                            "package_id": package_entry.package_id,
                            "package_version": package_entry.version,
                            "package_sha256": package_entry.sha256,
                            "tool_name": tool,
                            "parameters": runtime_parameters,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode("utf-8")
                    seed = int.from_bytes(hashlib.sha256(seed_basis).digest()[:8], "big")
                    if random.Random(seed).randint(1, 20) == desired_roll:
                        return suffix
                raise AssertionError(
                    f"could not select deterministic d20={desired_roll} for {prefix}"
                )

            def retry_intrusion_until(
                *,
                suffix: str,
                node_id: str,
                action: str,
                completed: Callable[[dict[str, Any]], bool],
                max_attempts: int = 20,
            ) -> None:
                if completed(network_state()):
                    return
                for attempt in range(1, max_attempts + 1):
                    execute(f"end-allies-{suffix}-{attempt}", "end_allies_turn", {})
                    execute(f"end-hostiles-{suffix}-{attempt}", "end_hostiles_turn", {})
                    execute(
                        f"{suffix}-{attempt}",
                        "intrude_network",
                        {"actor": "hex", "node_id": node_id, "action": action},
                    )
                    if completed(network_state()):
                        return
                raise AssertionError(
                    f"deterministic {action} qualification did not complete within "
                    f"{max_attempts + 1} attempts: {network_state()['last_network']!r}"
                )

            def retry_cqc_until(
                *,
                suffix: str,
                actor: str,
                target: str,
                maneuver: str,
                completed: Callable[[dict[str, Any], dict[str, Any]], bool],
                held_item: str | None = None,
                destination: list[int] | None = None,
                tool: str = "resolve_cqc",
                max_attempts: int = 20,
            ) -> None:
                for attempt in range(1, max_attempts + 1):
                    rules, entities = package_state()
                    if completed(rules, entities):
                        return
                    if rules["active_side"] == "hostiles":
                        execute(f"end-hostiles-{suffix}-{attempt}", "end_hostiles_turn", {})
                        rules, entities = package_state()
                    if entities[actor]["ap"] < 2:
                        execute(f"end-allies-{suffix}-{attempt}", "end_allies_turn", {})
                        execute(f"refresh-allies-{suffix}-{attempt}", "end_hostiles_turn", {})
                    parameters: dict[str, object] = {
                        "actor": actor,
                        "target": target,
                        "maneuver": maneuver,
                    }
                    if held_item is not None:
                        parameters["held_item"] = held_item
                    if destination is not None:
                        parameters["destination"] = destination
                    execute(f"{suffix}-{attempt}", tool, parameters)
                rules, entities = package_state()
                raise AssertionError(
                    f"deterministic {maneuver} qualification did not complete within "
                    f"{max_attempts} attempts: last_cqc={rules['last_cqc']!r}, "
                    f"actor={entities[actor]!r}, target={entities[target]!r}"
                )

            def retry_fire_until_hostile_neutralized(
                *, max_attempts: int = 20
            ) -> None:
                for attempt in range(1, max_attempts + 1):
                    rules, entities = package_state()
                    if entities["hostile_alpha"]["downed"]:
                        return
                    if rules["active_side"] == "hostiles":
                        execute(
                            f"end-hostiles-objective-fire-{attempt}",
                            "end_hostiles_turn",
                            {},
                        )
                        rules, entities = package_state()
                    recoil = entities["vesper"]["weapon"]["current_recoil"]
                    recovery_rate = entities["vesper"]["recoil_recovery_per_ap"]
                    recovery_ap = (
                        (recoil + recovery_rate - 1) // recovery_rate
                        if recoil > 0
                        else 0
                    )
                    if entities["vesper"]["ap"] < 2 + recovery_ap:
                        execute(
                            f"end-allies-objective-fire-{attempt}",
                            "end_allies_turn",
                            {},
                        )
                        execute(
                            f"refresh-allies-objective-fire-{attempt}",
                            "end_hostiles_turn",
                            {},
                        )
                    if recovery_ap:
                        execute(
                            f"recover-objective-fire-{attempt}",
                            "recover_recoil",
                            {"actor": "vesper", "ap": recovery_ap},
                        )
                    fire_parameters: dict[str, object] = {
                        "attacker": "vesper",
                        "target": "hostile_alpha",
                        "aim_point": [9.5, 2.5, 0.9],
                        "critical_effect": "DEVASTATING_HIT",
                    }
                    cover = next(
                        item
                        for item in scene_state()["geometry"]
                        if item["id"] == "partial-cover"
                    )
                    fire_tool = "fire_single"
                    desired_roll = 12
                    if cover["active"] and cover["metadata"]["cover_hp"] > 2:
                        desired_roll = 6
                    elif cover["active"]:
                        fire_tool = "fire_full_auto"
                        fire_parameters["rounds"] = 10
                        desired_roll = 20
                    execute(
                        d20_suffix(
                            prefix=f"objective-fire-{attempt}",
                            tool=fire_tool,
                            parameters=fire_parameters,
                            desired_roll=desired_roll,
                        ),
                        fire_tool,
                        fire_parameters,
                    )
                    rules_after_fire, _entities_after_fire = package_state()
                    expected_mode = (
                        "SEMI_AUTO" if fire_tool == "fire_single" else "FULL_AUTO"
                    )
                    expected_rounds = int(fire_parameters.get("rounds", 1))
                    last_attack = rules_after_fire["last_attack"]
                    if (
                        last_attack.get("mode") != expected_mode
                        or last_attack.get("rounds_fired") != expected_rounds
                    ):
                        raise AssertionError(
                            "fire action retained stale attack telemetry: "
                            f"tool={fire_tool!r}, last_attack={last_attack!r}"
                        )
                rules, entities = package_state()
                raise AssertionError(
                    "deterministic mixed-objective qualification did not neutralize "
                    f"Hostile Alpha within {max_attempts} attempts: "
                    f"last_attack={rules['last_attack']!r}, "
                    f"hostile={entities['hostile_alpha']!r}, "
                    f"vesper={entities['vesper']!r}"
                )

            execute_trigger(
                "prepare-overwatch",
                "prepare_overwatch",
                {"actor": "vesper", "zone_id": "overwatch-lane"},
            )
            execute_trigger(
                "establish-suppression",
                "establish_suppression",
                {"actor": "hex", "zone_id": "suppression-lane", "rounds": 10},
            )
            execute_trigger("open-hostile-turn", "end_allies_turn", {})
            pending_trigger_state = host["thaw"](
                owner.snapshot(TRIGGER_CAMPAIGN_ID).state
            )[f"{PACKAGE_ID}.state"]
            if (
                pending_trigger_state["prepared"]["overwatch"]["active"] is not True
                or pending_trigger_state["prepared"]["suppression"]["active"] is not True
            ):
                raise AssertionError(
                    "qualification did not persist both prepared fire states before reopen: "
                    f"{pending_trigger_state['prepared']!r}"
                )
            pending_trigger_revision = owner.snapshot(TRIGGER_CAMPAIGN_ID).revision

            catalog = tuple(item.name for item in owner.catalog(CAMPAIGN_ID))
            if len(catalog) != 23 or "end_allies_turn" not in catalog:
                raise AssertionError(f"unexpected initial catalog: {catalog}")
            boundary = owner.model_decision_boundary(CAMPAIGN_ID)
            if boundary is None or boundary.owner.value != "gm":
                raise AssertionError("side-based GM decision boundary was not projected")
            panels = host["thaw"](owner.campaign_panels(CAMPAIGN_ID))
            surfaces = {item["id"]: item for item in panels["surfaces"]}
            if set(surfaces) != {
                "operations.map",
                "operations.network",
                "operations.status",
            }:
                raise AssertionError(
                    f"expected tactical, network, and status surfaces, got {sorted(surfaces)}"
                )
            visible_map_ids = {
                item["id"] for item in surfaces["operations.map"]["data"]["entities"]
            }
            visible_network_ids = {
                item["id"] for item in surfaces["operations.network"]["data"]["nodes"]
            }
            if "hidden_hostile" in visible_map_ids or visible_network_ids != {
                "lobby_terminal"
            }:
                raise AssertionError("initial player panels disclosed private topology")
            participants = owner.snapshot(CAMPAIGN_ID).participants
            if len(participants) != 1 or participants[0].validation_status.value != "validated":
                raise AssertionError("conformance character was not schema validated")

            execute(
                "specialty-parameterized",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.networker.tier_2.foothold",
                    "role": "PRIMARY",
                    "benchmark_level": 10,
                    "prerequisite_mode": "SATISFIED",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if "status" not in specialty_state:
                raise AssertionError(
                    "parameterized specialty counterfactual produced no status: "
                    f"state={specialty_state!r}, actions={action_results[-3:]!r}"
                )
            if (
                specialty_state["status"] != "APPLIED"
                or specialty_state["node_effect_applied"] is not True
                or specialty_state["result"] == specialty_state["control"]
                or specialty_state["source"]["path"] != "Specialties/NETWORKER.md"
                or "specialty.networker.foothold.ap_discount"
                not in specialty_state["parameter_ids"]
                or not specialty_state["authored_rule_contract"]["activation_clauses"]
            ):
                raise AssertionError(
                    "parameterized specialty counterfactual did not execute from the "
                    f"compiled record: {specialty_state!r}"
                )
            execute(
                "specialty-structural",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.networker.entry.map_the_links",
                    "role": "SECONDARY",
                    "benchmark_level": 10,
                    "prerequisite_mode": "SATISFIED",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if (
                specialty_state["status"] != "APPLIED"
                or specialty_state["mechanic_id"] != "network_discovery"
                or not specialty_state["changed_fields"]
            ):
                raise AssertionError(
                    "structural specialty counterfactual did not execute from the "
                    f"compiled record: {specialty_state!r}"
                )
            execute(
                "specialty-guard-violation",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.networker.tier_2.segment_map",
                    "role": "PRIMARY",
                    "benchmark_level": 10,
                    "prerequisite_mode": "SATISFIED",
                    "boundary_mode": "FIRST_GUARD_VIOLATED",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if (
                specialty_state["status"] != "GUARD_VIOLATION"
                or specialty_state["violated_field"]
                != "systems_not_discoverable_from_actual_position_revealed"
                or specialty_state["node_effect_applied"] is not False
                or specialty_state["mechanical_operations_executed"]
                or specialty_state["result"] != specialty_state["control"]
            ):
                raise AssertionError(
                    "specialty guard fixture did not block before its node effect: "
                    f"{specialty_state!r}"
                )
            execute(
                "specialty-termination",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.networker.tier_2.foothold",
                    "role": "PRIMARY",
                    "benchmark_level": 10,
                    "prerequisite_mode": "SATISFIED",
                    "boundary_mode": "FIRST_TERMINATION",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if (
                specialty_state["status"] != "TERMINATED"
                or specialty_state["violated_field"]
                != "foothold_survives_proper_remediation_or_destruction"
                or specialty_state["effect_established_before_termination"] is not True
                or specialty_state["node_effect_applied"] is not False
                or not specialty_state["mechanical_operations_executed"]
            ):
                raise AssertionError(
                    "specialty termination fixture did not establish then end its effect: "
                    f"{specialty_state!r}"
                )
            execute(
                "specialty-invariant-mutation",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.networker.entry.map_the_links",
                    "role": "PRIMARY",
                    "benchmark_level": 10,
                    "prerequisite_mode": "SATISFIED",
                    "boundary_mode": "FIRST_INVARIANT_MUTATION",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if (
                specialty_state["status"] != "INVARIANT_VIOLATION"
                or specialty_state["violated_field"]
                != "hidden_or_unseen_topology_revealed"
                or specialty_state["node_effect_applied"] is not True
                or specialty_state["limitations_preserved"] is not False
            ):
                raise AssertionError(
                    "specialty invariant mutation fixture was not detected after the effect: "
                    f"{specialty_state!r}"
                )
            execute(
                "specialty-off-role-node-present",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.networker.tier_2.foothold",
                    "role": "OFF_ROLE",
                    "benchmark_level": 10,
                    "prerequisite_mode": "SATISFIED",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if (
                specialty_state["status"] != "CONTROL"
                or specialty_state["node_effect_applied"] is not False
                or specialty_state["result"] != specialty_state["control"]
            ):
                raise AssertionError(
                    "off-role same-character fixture did not retain the node as a "
                    f"non-applying control: {specialty_state!r}"
                )
            execute(
                "specialty-off-role-node-missing",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.local_hacker.entry.service_access",
                    "role": "OFF_ROLE",
                    "benchmark_level": 10,
                    "prerequisite_mode": "SATISFIED",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if (
                specialty_state["status"] != "ACTOR_MISSING_NODE"
                or specialty_state["node_effect_applied"] is not False
                or specialty_state["result"] != specialty_state["control"]
            ):
                raise AssertionError(
                    "off-role fixture accepted an actor who did not own the tested "
                    f"node: {specialty_state!r}"
                )
            execute(
                "specialty-missing-prerequisite",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.networker.tier_2.foothold",
                    "role": "PRIMARY",
                    "benchmark_level": 10,
                    "prerequisite_mode": "FIRST_MISSING",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if (
                specialty_state["status"] != "MISSING_PREREQUISITE"
                or specialty_state["node_effect_applied"] is not False
                or specialty_state["result"] != specialty_state["control"]
                or specialty_state["prerequisite_fixture"]
                == specialty_state["prerequisite_contract"]
            ):
                raise AssertionError(
                    "specialty prerequisite-negative fixture applied or failed to "
                    f"record the missing prerequisite: {specialty_state!r}"
                )
            execute(
                "specialty-wrong-actor",
                "exercise_specialty_counterfactual",
                {
                    "actor": "hex",
                    "node_id": "specialty.anchor.tier_5.fortress",
                    "role": "PRIMARY",
                    "benchmark_level": 10,
                    "prerequisite_mode": "SATISFIED",
                },
            )
            specialty_state = network_state()["last_specialty_test"]
            if specialty_state["status"] not in {"WRONG_CLASS", "ACTOR_MISSING_NODE"}:
                raise AssertionError(
                    "specialty fixture accepted a node the exact actor cannot execute: "
                    f"{specialty_state!r}"
                )

            autodrone_parameters: dict[str, object] = {
                "actor": "autodrone_alpha",
                "target": "drone_target",
                "aim_point": [31.5, 30.5, 0.9],
            }
            _, autodrone_result = execute(
                d20_suffix(
                    prefix="autodrone-fire",
                    tool="autodrone_fire",
                    parameters=autodrone_parameters,
                    desired_roll=20,
                ),
                "autodrone_fire",
                autodrone_parameters,
            )
            _, armed_entities = package_state()
            if (
                armed_entities["autodrone_alpha"]["ap"] != 0
                or armed_entities["autodrone_alpha"]["weapon"]["magazine"]["rounds"]
                != 9
                or armed_entities["autodrone_alpha"]["weapon"]["current_recoil"]
                != 2
                or armed_entities["drone_target"]["hp"] >= 50
            ):
                raise AssertionError(
                    "autonomous firearm did not consume execution AP, ammunition, "
                    f"RECOIL, and target state: {armed_entities!r}"
                )
            fpv_parameters: dict[str, object] = {
                "actor": "hex",
                "drone": "fpv_alpha",
                "target": "drone_target",
                "landing_point": [31.0, 30.5, 0.5],
            }
            _, fpv_result = execute(
                d20_suffix(
                    prefix="fpv-strike",
                    tool="fpv_fragmentation_strike",
                    parameters=fpv_parameters,
                    desired_roll=20,
                ),
                "fpv_fragmentation_strike",
                fpv_parameters,
            )
            fpv_rules, fpv_entities = package_state()
            fpv_scene = next(
                item
                for item in fpv_rules["spatial_scene"]["entities"]
                if item["id"] == "fpv_alpha"
            )
            fpv_payload = host["thaw"](fpv_result.result)
            if "spatial_evidence" not in fpv_payload:
                raise AssertionError(
                    "FPV strike returned no spatial evidence: "
                    f"status={fpv_result.status.value!r}, result={fpv_payload!r}"
                )
            fpv_evidence = fpv_payload["spatial_evidence"]
            blast_snapshot = next(
                evidence
                for evidence in fpv_evidence
                if evidence["operation"] == "snapshot"
                and evidence["geometry_revision"]
                == fpv_rules["last_drone_attack"]["geometry_revision"]
            )
            blast_followups = [
                evidence
                for evidence in fpv_evidence
                if evidence["operation"] in {"area", "ray"}
                and evidence["geometry_revision"]
                == blast_snapshot["geometry_revision"]
            ]
            if (
                fpv_entities["fpv_alpha"]["active"] is not False
                or fpv_entities["fpv_alpha"]["payload"] is not None
                or fpv_scene["active"] is not False
                or fpv_rules["last_drone_attack"]["status"] != "DETONATED"
                or fpv_rules["last_drone_attack"]["placement_success"] is not True
                or len(blast_followups) < 2
            ):
                raise AssertionError(
                    "FPV strike did not consume the exact drone or preserve one blast "
                    f"snapshot: rules={fpv_rules['last_drone_attack']!r}, "
                    f"entity={fpv_entities['fpv_alpha']!r}, scene={fpv_scene!r}, "
                    f"evidence={fpv_evidence!r}"
                )
            execute("end-allies-drone-proof", "end_allies_turn", {})
            execute("end-hostiles-drone-proof", "end_hostiles_turn", {})

            aim_command, aim_result = execute(
                "aim-vesper",
                "aim",
                {
                    "attacker": "vesper",
                    "target": "hostile_alpha",
                    "aim_point": [9.5, 2.5, 0.9],
                    "ap": 1,
                },
            )
            execute(
                "get-in-lobby-a",
                "intrude_network",
                {"actor": "hex", "node_id": "lobby_terminal", "action": "GET_IN"},
            )
            pre_rejected_operate_rules, pre_rejected_operate_entities = package_state()
            execute(
                "operate-security-without-privilege",
                "operate_remote_function",
                {
                    "actor": "hex",
                    "node_id": "security_controller",
                    "remote_function": "open_security_door",
                },
            )
            rejected_operate_rules, rejected_operate_entities = package_state()
            rejected_operate = rejected_operate_rules["last_network"]
            if (
                rejected_operate
                != {
                    "action": "OPERATE",
                    "node": "security_controller",
                    "remote_function": "open_security_door",
                    "status": "OPERATE_REJECTED",
                    "success": False,
                }
                or rejected_operate_entities["hex"]["ap"]
                != pre_rejected_operate_entities["hex"]["ap"]
                or rejected_operate_rules["network"]["revision"]
                != pre_rejected_operate_rules["network"]["revision"]
            ):
                raise AssertionError(
                    "rejected OPERATE retained stale intrusion telemetry or mutated AP/network: "
                    f"last_network={rejected_operate!r}, "
                    f"before_ap={pre_rejected_operate_entities['hex']['ap']}, "
                    f"after_ap={rejected_operate_entities['hex']['ap']}"
                )
            execute(
                "fire-vesper",
                "fire_single",
                {
                    "attacker": "vesper",
                    "target": "hostile_alpha",
                    "aim_point": [9.5, 2.5, 0.9],
                    "critical_effect": "DEVASTATING_HIT",
                },
            )
            state = host["thaw"](owner.snapshot(CAMPAIGN_ID).state)
            entities = state[f"{PACKAGE_ID}.entities"]
            if entities["vesper"]["ap"] != 3 or entities["hex"]["ap"] != 5:
                raise AssertionError("interleaved firearm/network actions did not spend independent AP")
            execute(
                "fire-hex-after-network",
                "fire_single",
                {
                    "attacker": "hex",
                    "target": "hostile_alpha",
                    "aim_point": [9.5, 2.5, 0.9],
                    "critical_effect": "DEEP_HIT",
                },
            )
            _, networker_fire_entities = package_state()
            if (
                networker_fire_entities["hex"]["ap"] != 3
                or networker_fire_entities["hex"]["weapon"]["magazine"]["rounds"]
                != 29
                or networker_fire_entities["hex"]["weapon"]["chamber"] != "LIVE"
            ):
                raise AssertionError(
                    "Networker firearm choice did not use the same AP and chamber/feed state: "
                    f"{networker_fire_entities['hex']!r}"
                )
            execute(
                "retain-reload-vesper",
                "reload_weapon",
                {"actor": "vesper", "disposition": "RETAIN"},
            )
            _, reloaded_entities = package_state()
            vesper_weapon = reloaded_entities["vesper"]["weapon"]
            if (
                reloaded_entities["vesper"]["ap"] != 1
                or vesper_weapon["magazine"]["rounds"] != 30
                or vesper_weapon["retained_magazines"]
                != [{"id": "vesper-service_rifle-magazine-0", "rounds": 29}]
                or len(vesper_weapon["spare_magazines"]) != 1
            ):
                raise AssertionError(
                    "detachable reload did not retain the physical partial magazine"
                )
            execute("end-allies-manual-cycle", "end_allies_turn", {})
            execute(
                "cycle-hostile-manual-action",
                "cycle_weapon",
                {"actor": "hostile_alpha"},
            )
            _, cycled_entities = package_state()
            hostile_weapon = cycled_entities["hostile_alpha"]["weapon"]
            if (
                cycled_entities["hostile_alpha"]["ap"] != 5
                or hostile_weapon["chamber"] != "LIVE"
                or hostile_weapon["needs_cycle"] is not False
                or hostile_weapon["magazine"]["rounds"] != 4
            ):
                raise AssertionError(
                    "manual-action cycle did not spend declared AP and feed one round"
                )
            execute("end-hostiles-manual-cycle", "end_hostiles_turn", {})
            execute(
                "drop-reload-vesper",
                "reload_weapon",
                {"actor": "vesper", "disposition": "DROP"},
            )
            dropped_rules, dropped_entities = package_state()
            if (
                dropped_entities["vesper"]["ap"] != 4
                or dropped_entities["vesper"]["weapon"]["magazine"]["rounds"] != 30
                or dropped_rules["ground_objects"].get("magazine_1")
                != {
                    "id": "vesper-service_rifle-magazine-2",
                    "kind": "MAGAZINE",
                    "position_cell": [2, 2],
                    "rounds": 30,
                }
            ):
                raise AssertionError(
                    "drop reload did not preserve the ejected magazine as a ground object"
                )
            retry_intrusion_until(
                suffix="get-in-lobby-retry",
                node_id="lobby_terminal",
                action="GET_IN",
                completed=lambda current: current["network"]["nodes"]["lobby_terminal"]["control"]
                == "compromised",
            )

            execute(
                "pivot-camera-c",
                "intrude_network",
                {"actor": "hex", "node_id": "camera_gateway", "action": "PIVOT"},
            )
            retry_intrusion_until(
                suffix="pivot-camera-retry",
                node_id="camera_gateway",
                action="PIVOT",
                completed=lambda current: current["network"]["nodes"]["camera_gateway"]["control"]
                == "compromised",
            )
            execute(
                "pivot-security",
                "intrude_network",
                {"actor": "hex", "node_id": "security_controller", "action": "PIVOT"},
            )
            retry_intrusion_until(
                suffix="pivot-security-retry",
                node_id="security_controller",
                action="PIVOT",
                completed=lambda current: current["network"]["nodes"]["security_controller"]["control"]
                == "compromised",
            )
            execute("end-allies-1", "end_allies_turn", {})
            execute("end-hostiles-1", "end_hostiles_turn", {})
            execute(
                "escalate-security-b",
                "intrude_network",
                {"actor": "hex", "node_id": "security_controller", "action": "ESCALATE"},
            )
            retry_intrusion_until(
                suffix="escalate-security-retry",
                node_id="security_controller",
                action="ESCALATE",
                completed=lambda current: current["network_access"]["security_controller"]["privilege"]
                >= 2,
            )
            current_panels = host["thaw"](owner.campaign_panels(CAMPAIGN_ID))
            current_network = next(
                item for item in current_panels["surfaces"] if item["id"] == "operations.network"
            )
            pre_operate_state = host["thaw"](owner.snapshot(CAMPAIGN_ID).state)[
                f"{PACKAGE_ID}.state"
            ]
            if (
                pre_operate_state["network"]["nodes"]["security_controller"]["control"]
                != "compromised"
                or pre_operate_state["network_access"]["security_controller"]["privilege"]
                < 2
            ):
                raise AssertionError(
                    "deterministic network traversal did not reach OPERATE authority: "
                    f"state={pre_operate_state['last_network']!r}, "
                    f"access={pre_operate_state['network_access']!r}, "
                    f"actions={action_results[-5:]!r}"
                )
            panel_result = owner.execute_panel_action(
                campaign_id=CAMPAIGN_ID,
                surface_id="operations.network",
                panel_action_id="operate",
                operation_id="td-panel-operate-door",
                expected_revision=owner.snapshot(CAMPAIGN_ID).revision,
                selection={
                    "graph_revision": current_network["data"]["graph_revision"],
                    "node": "security_controller",
                    "remote_function": "open_security_door",
                },
            )
            action_results.append(
                {
                    "tool": "operate_remote_function",
                    "action_id": "td-panel-operate-door",
                    "dispatch": "network_graph_panel",
                    "revision": panel_result.resulting_revision,
                    "status": panel_result.status.value,
                    "result": host["thaw"](panel_result.result),
                    "result_sha256": _sha256(
                        json.dumps(
                            host["thaw"](panel_result.result),
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ),
                }
            )
            operated_scene = scene_state()
            operated_door = next(
                item for item in operated_scene["geometry"] if item["id"] == "security-door"
            )
            operated_rules, _ = package_state()
            operated_network = operated_rules["last_network"]
            if (
                operated_scene["map"]["revision"]
                != pre_operate_state["spatial_scene"]["map"]["revision"] + 1
                or operated_door["active"] is not False
                or operated_door["movement"] != "passable"
                or operated_door["visibility"] != "clear"
                or operated_door["metadata"]["player"]["state"] != "open"
                or operated_network
                != {
                    "action": "OPERATE",
                    "geometry_revision": operated_scene["map"]["revision"],
                    "node": "security_controller",
                    "remote_function": "open_security_door",
                    "status": "OPERATED",
                    "success": True,
                }
            ):
                raise AssertionError(
                    "OPERATE did not mutate the exact security-door geometry: "
                    f"revision={operated_scene['map']['revision']!r}, door={operated_door!r}, "
                    f"last_network={operated_network!r}"
                )
            retry_fire_until_hostile_neutralized()
            objective_rules, objective_entities = package_state()
            if (
                objective_rules["mission"]
                != {"status": "COMPLETE", "hostile_neutralized": True}
                or objective_rules["physical"]["security_door_open"] is not True
                or objective_entities["hostile_alpha"]["downed"] is not True
                or {item["id"]: item["status"] for item in objective_rules["objectives"]}
                != {
                    "neutralize-hostile-alpha": "complete",
                    "open-security-door": "complete",
                }
            ):
                raise AssertionError(
                    "mixed tactical/network objective did not require and preserve "
                    f"both outcomes: rules={objective_rules!r}, "
                    f"hostile={objective_entities['hostile_alpha']!r}"
                )
            retry_cqc_until(
                suffix="body-control",
                actor="vesper",
                target="hex",
                maneuver="BODY_CONTROL",
                completed=lambda _rules, entities: entities["hex"]["control"]
                == {
                    "controller": "vesper",
                    "focus": "BODY_CONTROL",
                },
            )
            retry_cqc_until(
                suffix="escape-body-control",
                actor="hex",
                target="vesper",
                maneuver="ESCAPE",
                completed=lambda _rules, entities: entities["hex"]["control"] is None
                and entities["vesper"]["control"] is None,
            )
            retry_cqc_until(
                suffix="weapon-control",
                actor="vesper",
                target="hex",
                maneuver="WEAPON_CONTROL",
                held_item="PRIMARY_WEAPON",
                completed=lambda _rules, entities: entities["hex"]["weapon"][
                    "controlled_by"
                ]
                == "vesper",
            )
            controlled_rules, controlled_entities = package_state()
            if controlled_entities["hex"]["ap"] < 1:
                execute("end-allies-weapon-control-other-action", "end_allies_turn", {})
                execute(
                    "refresh-allies-weapon-control-other-action",
                    "end_hostiles_turn",
                    {},
                )
                controlled_rules, controlled_entities = package_state()
            controlled_hex_ap = controlled_entities["hex"]["ap"]
            execute(
                "weapon-control-blocks-aim",
                "aim",
                {
                    "attacker": "hex",
                    "target": "hostile_alpha",
                    "aim_point": [9.5, 2.5, 0.9],
                    "ap": 1,
                },
            )
            controlled_rules, controlled_entities = package_state()
            if (
                controlled_entities["hex"]["ap"] != controlled_hex_ap
                or controlled_rules["last_attack"].get("outcome") != "AIM_REJECTED"
            ):
                raise AssertionError(
                    "WEAPON CONTROL did not block the declared weapon without spending AP"
                )
            execute(
                "weapon-control-allows-other-action",
                "resolve_check",
                {
                    "actor": "hex",
                    "skill": "networking",
                    "aptitude": "reason",
                    "difficulty": 2,
                    "modifier": 0,
                },
            )
            controlled_rules_after_check, controlled_entities_after_check = package_state()
            if (
                controlled_entities_after_check["hex"]["ap"] != controlled_hex_ap
                or controlled_rules_after_check["last_check"].get("actor") != "hex"
            ):
                raise AssertionError("WEAPON CONTROL incorrectly acted as a global stun")
            retry_cqc_until(
                suffix="escape-weapon-control",
                actor="hex",
                target="vesper",
                maneuver="ESCAPE",
                completed=lambda _rules, entities: entities["hex"]["control"] is None
                and entities["vesper"]["control"] is None
                and entities["hex"]["weapon"]["controlled_by"] is None,
            )
            retry_cqc_until(
                suffix="disarm",
                actor="vesper",
                target="hex",
                maneuver="DISARM",
                held_item="PRIMARY_WEAPON",
                completed=lambda rules, entities: entities["hex"]["weapon"]["location"]
                == "GROUND"
                and rules["ground_objects"].get("hex_primary_weapon")
                == {"id": "service_rifle", "position_cell": [2, 3]},
            )
            retry_cqc_until(
                suffix="trip",
                actor="vesper",
                target="hex",
                maneuver="TRIP",
                completed=lambda _rules, entities: entities["hex"]["stance"] == "PRONE",
            )
            retry_cqc_until(
                suffix="shove",
                actor="vesper",
                target="hex",
                maneuver="SHOVE",
                destination=[3, 3],
                tool="resolve_cqc_displacement",
                completed=lambda _rules, entities: entities["hex"]["position_cell"]
                == [3, 3],
            )
            shoved_scene = scene_state()
            shoved_hex = next(
                item for item in shoved_scene["entities"] if item["id"] == "hex"
            )
            if shoved_hex["position"] != [3.5, 3.5, 0]:
                raise AssertionError(
                    "SHOVE did not synchronize the exact scene entity: "
                    f"hex={shoved_hex!r}"
                )
            retry_cqc_until(
                suffix="throw",
                actor="vesper",
                target="hex",
                maneuver="THROW",
                destination=[4, 3],
                tool="resolve_cqc_displacement",
                completed=lambda _rules, entities: entities["hex"]["position_cell"]
                == [4, 3]
                and entities["hex"]["stance"] == "PRONE",
            )
            _, post_cqc_entities = package_state()
            if post_cqc_entities["vesper"]["ap"] < 2:
                execute("end-allies-post-cqc", "end_allies_turn", {})
                execute("refresh-allies-post-cqc", "end_hostiles_turn", {})
            before_rejections = host["thaw"](owner.snapshot(CAMPAIGN_ID).state)
            before_rejection_entities = before_rejections[f"{PACKAGE_ID}.entities"]
            before_rejection_rules = before_rejections[f"{PACKAGE_ID}.state"]
            execute(
                "out-of-reach-cqc",
                "resolve_cqc",
                {"actor": "vesper", "target": "hostile_alpha", "maneuver": "TRIP"},
            )
            execute(
                "invalid-pivot-lobby",
                "intrude_network",
                {"actor": "hex", "node_id": "lobby_terminal", "action": "PIVOT"},
            )
            rejected_state = host["thaw"](owner.snapshot(CAMPAIGN_ID).state)
            rejected_entities = rejected_state[f"{PACKAGE_ID}.entities"]
            rejected_rules = rejected_state[f"{PACKAGE_ID}.state"]
            if (
                rejected_entities["vesper"]["ap"]
                != before_rejection_entities["vesper"]["ap"]
                or rejected_rules["last_cqc"].get("status") != "OUT_OF_REACH_OR_NO_AP"
            ):
                raise AssertionError("out-of-reach CQC did not fail without spending AP")
            if (
                rejected_entities["hex"]["ap"] != before_rejection_entities["hex"]["ap"]
                or rejected_rules["last_network"].get("status") != "MISSING_PREREQUISITE"
                or rejected_rules["network"]["revision"]
                != before_rejection_rules["network"]["revision"]
            ):
                raise AssertionError("network action-specific prerequisites were not enforced")
            execute(
                "trigger-crossing",
                "inspect_trigger_crossing",
                {"actor": "vesper", "start": [4.0, 2.0, 0.9], "end": [8.0, 2.0, 0.9]},
            )
            _, blast_result = execute(
                "blast-snapshot",
                "resolve_blast_snapshot",
                {"actor": "vesper", "center": [6.25, 4.5, 0.0], "radius": 3.0},
            )
            _, move_result = execute(
                "move-vesper",
                "move_actor",
                {"actor": "vesper", "destination": [3, 2]},
            )
            final_state = host["thaw"](owner.snapshot(CAMPAIGN_ID).state)
            rules_state = final_state[f"{PACKAGE_ID}.state"]
            if rules_state["physical"]["security_door_open"] is not True:
                raise AssertionError(
                    "remote function did not update the ruleset physical state: "
                    f"last_network={rules_state['last_network']!r}, "
                    f"network_access={rules_state['network_access']!r}, "
                    f"nodes={rules_state['network']['nodes']!r}, "
                    f"actions={action_results!r}"
                )
            if rules_state["mission"]["status"] != "COMPLETE":
                raise AssertionError("mixed tactical/network mission did not remain complete")
            blast_evidence = host["thaw"](blast_result.result)["spatial_evidence"]
            blast_revisions = {item["geometry_revision"] for item in blast_evidence}
            if (
                len(blast_revisions) != 1
                or rules_state["last_blast"]["geometry_revision"]
                != next(iter(blast_revisions))
            ):
                raise AssertionError(
                    "simultaneous blast did not preserve one frozen geometry revision: "
                    f"last_blast={rules_state['last_blast']!r}, evidence={blast_evidence!r}"
                )
            cover_mutation_action = next(
                (
                    item
                    for item in action_results
                    if item["tool"] == "fire_full_auto"
                    and any(
                        evidence["operation"] == "scene_mutation"
                        and evidence.get("target")
                        == {"kind": "geometry", "id": "partial-cover"}
                        for evidence in item["result"].get("spatial_evidence", [])
                    )
                ),
                None,
            )
            if cover_mutation_action is None:
                raise AssertionError(
                    "qualification did not exercise a sequential FULL AUTO cover mutation: "
                    f"full_auto_actions={[item for item in action_results if item['tool'] == 'fire_full_auto']!r}"
                )
            cover_mutation_command = action_commands[cover_mutation_action["action_id"]]
            cover_mutation_result_sha256 = cover_mutation_action["result_sha256"]
            cover_mutation_revision = cover_mutation_action["revision"]
            burst_evidence = cover_mutation_action["result"]["spatial_evidence"]
            mutation_evidence_index = next(
                index
                for index, evidence in enumerate(burst_evidence)
                if evidence["operation"] == "scene_mutation"
                and evidence.get("target")
                == {"kind": "geometry", "id": "partial-cover"}
            )
            cover_mutation = next(
                evidence
                for evidence in burst_evidence
                if evidence["operation"] == "scene_mutation"
                and evidence.get("target")
                == {"kind": "geometry", "id": "partial-cover"}
            )
            next_projectile = next(
                evidence
                for evidence in burst_evidence[mutation_evidence_index + 1 :]
                if evidence["operation"] == "snapshot"
            )
            if (
                next_projectile["geometry_revision"]
                != cover_mutation["after"]["geometry_revision"]
            ):
                raise AssertionError(
                    "sequential projectile reused stale geometry after cover mutation: "
                    f"mutation={cover_mutation!r}, next={next_projectile!r}"
                )
            final_scene = rules_state["spatial_scene"]
            final_vesper = next(
                item for item in final_scene["entities"] if item["id"] == "vesper"
            )
            final_hex = next(
                item for item in final_scene["entities"] if item["id"] == "hex"
            )
            if (
                final_state[f"{PACKAGE_ID}.entities"]["vesper"]["position_cell"] != [3, 2]
                or final_state[f"{PACKAGE_ID}.entities"]["vesper"]["center"] != [3.5, 2.5, 0.9]
                or final_state[f"{PACKAGE_ID}.entities"]["vesper"]["muzzle"] != [3.91, 2.5, 1.4]
                or final_vesper["position"] != [3.5, 2.5, 0]
                or final_state[f"{PACKAGE_ID}.entities"]["hex"]["position_cell"] != [4, 3]
                or final_state[f"{PACKAGE_ID}.entities"]["hex"]["center"] != [4.5, 3.5, 0.9]
                or final_state[f"{PACKAGE_ID}.entities"]["hex"]["muzzle"] != [4.91, 3.5, 1.4]
                or final_hex["position"] != [4.5, 3.5, 0]
                or rules_state["last_move"]["status"] != "MOVED"
            ):
                raise AssertionError(
                    "entity-aware movement or CQC scene synchronization failed: "
                    f"position={final_state[f'{PACKAGE_ID}.entities']['vesper']['position_cell']!r}, "
                    f"scene={final_vesper!r}, last_move={rules_state['last_move']!r}, "
                    f"action={host['thaw'](move_result.result)!r}"
                )
            final_revision = owner.snapshot(CAMPAIGN_ID).revision
            final_state_sha256 = _sha256(
                json.dumps(
                    final_state, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )

        with host["AuthorityOwner"].open(database, OWNER_ID) as reopened:
            if reopened.snapshot(CAMPAIGN_ID).revision != final_revision:
                raise AssertionError("campaign revision did not survive reopen")
            reopened_mechanics = host["Mechanics"](reopened)
            replay = reopened_mechanics.execute_action(cover_mutation_command)
            replay_payload = host["thaw"](replay.result)
            if (
                replay.resulting_revision != cover_mutation_revision
                or _sha256(
                    json.dumps(
                        replay_payload, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                )
                != cover_mutation_result_sha256
            ):
                raise AssertionError(
                    "spatial/dice/scene-mutation replay did not return its original result"
                )
            persisted = host["thaw"](reopened.snapshot(CAMPAIGN_ID).state)
            if (
                _sha256(
                    json.dumps(
                        persisted, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                )
                != final_state_sha256
            ):
                raise AssertionError("meaningful replay changed the completed campaign state")
            if persisted[f"{PACKAGE_ID}.state"]["physical"]["security_door_open"] is not True:
                raise AssertionError("network-to-physical state did not survive reopen")
            if persisted[f"{PACKAGE_ID}.state"]["mission"]["status"] != "COMPLETE":
                raise AssertionError("mixed-objective completion did not survive reopen")

            if reopened.snapshot(TRIGGER_CAMPAIGN_ID).revision != pending_trigger_revision:
                raise AssertionError("pending trigger revision did not survive reopen")
            pending_after_reopen = host["thaw"](
                reopened.snapshot(TRIGGER_CAMPAIGN_ID).state
            )[f"{PACKAGE_ID}.state"]
            if (
                pending_after_reopen["prepared"]["overwatch"]["active"] is not True
                or pending_after_reopen["prepared"]["suppression"]["active"] is not True
            ):
                raise AssertionError("prepared trigger state did not survive reopen")
            trigger_command = host["ActionCommand"](
                campaign_id=TRIGGER_CAMPAIGN_ID,
                action_id="td-trigger-resolve-after-reopen",
                idempotency_key="td-idempotency-trigger-resolve-after-reopen",
                owner_id=OWNER_ID,
                writer=WriterRole.DM,
                expected_revision=pending_trigger_revision,
                turn_id=f"{TURN_ID}-triggers",
                package_id=PACKAGE_ID,
                tool_name="resolve_trigger_crossing",
                parameters={
                    "actor": "hostile_alpha",
                    "overwatcher": "vesper",
                    "suppressor": "hex",
                    "destination": [4, 2],
                    "first_effect": "OVERWATCH",
                    "overwatch_critical_effect": "DEEP_HIT",
                    "suppression_critical_effect": "DEVASTATING_HIT",
                },
            )
            trigger_result = reopened_mechanics.execute_action(trigger_command)
            trigger_payload = host["thaw"](trigger_result.result)
            trigger_state = host["thaw"](
                reopened.snapshot(TRIGGER_CAMPAIGN_ID).state
            )
            trigger_rules = trigger_state[f"{PACKAGE_ID}.state"]
            trigger_entities = trigger_state[f"{PACKAGE_ID}.entities"]
            if (
                trigger_rules["last_trigger"].get("status")
                not in {"RESUMED", "INTERRUPTED"}
                or trigger_rules["last_trigger"].get("first_effect") != "OVERWATCH"
                or trigger_rules["last_trigger"].get("overwatch", {}).get("status")
                != "RESOLVED"
                or trigger_rules["last_trigger"].get("suppression", {}).get("status")
                != "RESOLVED"
            ):
                raise AssertionError(
                    "reopened prepared crossing did not resolve both effects: "
                    f"last_trigger={trigger_rules['last_trigger']!r}, "
                    f"result={trigger_payload!r}, prepared={trigger_rules['prepared']!r}, "
                    f"active_side={trigger_rules['active_side']!r}"
                )
            if (
                trigger_rules["last_trigger"]["status"] == "RESUMED"
                and trigger_entities["hostile_alpha"]["position_cell"] != [4, 2]
            ):
                raise AssertionError("surviving interrupted movement did not resume")
            trigger_revision = trigger_result.resulting_revision
            trigger_state_sha256 = _sha256(
                json.dumps(
                    trigger_state, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
            trigger_replay = reopened_mechanics.execute_action(trigger_command)
            if (
                trigger_replay.resulting_revision != trigger_revision
                or host["thaw"](trigger_replay.result) != trigger_payload
                or _sha256(
                    json.dumps(
                        host["thaw"](
                            reopened.snapshot(TRIGGER_CAMPAIGN_ID).state
                        ),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                != trigger_state_sha256
            ):
                raise AssertionError("trigger resolution replay was not idempotent")

    result: dict[str, Any] = {
        "format": 1,
        "status": "PASS",
        "package": {
            "artifact": first["artifact"],
            "archive_sha256": _sha256(first_bytes),
            "closure_sha256": first["closure_sha256"],
            "content_sha256": first["package_sha256"],
            "setting_content_sha256": first["setting_sha256"],
            "package_count": 2,
            "byte_deterministic": True,
        },
        "sylvia_commit": first["sylvia_commit"],
        "source_commit": scenario["source_commit"],
        "parameter_set_sha256": scenario["parameter_set_sha256"],
        "campaign": {
            "explicit_activation_id": activation.activation_id,
            "explicit_activation_revision": activation.resulting_revision,
            "catalog_tools": list(catalog),
            "surface_ids": sorted(surfaces),
            "initial_state_sha256": _sha256(
                json.dumps(initial, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ),
            "final_state_sha256": final_state_sha256,
            "initial_decision_actor": boundary.actor_id,
            "initial_visible_map_entities": sorted(visible_map_ids),
            "initial_visible_network_nodes": sorted(visible_network_ids),
            "participant_status": participants[0].validation_status.value,
            "campaign_creation_ruleset": creation_ruleset,
            "invalid_character_rejected": invalid_character_rejected,
            "final_revision": final_revision,
            "idempotent_replay_action_id": cover_mutation_command.action_id,
            "idempotent_replay_revision": cover_mutation_revision,
            "pending_trigger_revision": pending_trigger_revision,
            "resolved_trigger_revision": trigger_revision,
            "trigger_state_sha256": trigger_state_sha256,
            "trigger_actions_before_reopen": trigger_action_results,
            "trigger_resolution": trigger_payload,
            "actions": action_results,
        },
        "known_host_dependencies": [
            {
                "code": "SYLVIA_RAY_GRID_INTERSECTION_NONTERMINATION",
                "sylvia_commit": first["sylvia_commit"],
                "status": "MITIGATED_PACKAGE_COMPATIBILITY_GAP",
                "reproduction": {
                    "algorithm_version": "sylvia.spatial.supercover.v1",
                    "origin": [2.91, 2.5, 1.4],
                    "destination": [8.0, 1.0, 0.9],
                    "map_origin": [0, 0],
                    "cell_size": 1,
                },
                "package_mitigation": "keep the mover at the exact crossing and raycast to [x + 0.000001, y + 0.000001, z] inside the same occupied body volume",
                "required_host_change": "terminate the universal supercover walk at the destination cell when either axis has already reached its target coordinate",
            }
        ],
    }
    report = root / "ruleset-draft" / "output" / f"qualification-{first['closure_sha256']}.json"
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["report"] = report.relative_to(root).as_posix()
    return result
