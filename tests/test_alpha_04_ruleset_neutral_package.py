"""RULE-005 immutable Format-3 package acceptance through sole authority."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics, _model_input_schema
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_runtime import (
    PackageRuntimeError,
    materialize_package_mechanic,
    validate_package_runtime,
)
from sylvia_gm.domain.authority import (
    ActionStatus,
    MechanicContext,
    ModelDecisionOwner,
    StateMutation,
    StateValue,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import (
    ToolDeclaration,
    canonical_json_bytes,
    content_root_for_record,
)
from sylvia_gm.runtime._effect_executor import (
    _COMPATIBILITY_EFFECT_KEYS,
    _EFFECT_HANDLERS,
    _NEUTRAL_EFFECT_KEYS,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "tests" / "fixtures" / "rulesets" / "signal-and-static"
PACKAGE_ID = "sylvia-gm.conformance.signal-static"
CAMPAIGN_ID = "campaign-rule005-signal-static"
TURN_ID = "turn-rule005-signal-static"


def test_srd_d20_model_contract_hides_and_derives_authoritative_character_facts() -> None:
    ruleset_root = Path(__file__).parents[1] / "rulesets" / "srd-lite"
    record_path = ruleset_root / "package-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in record["tools"]}
    schema, derivations = _model_input_schema(
        tools["d20_test"]["input_schema"],
        participant_ids=("rift-player-character",),
    )

    assert set(schema["required"]) == {
        "actor",
        "skill",
        "circumstantial_modifier",
        "target_number",
    }
    assert schema["properties"]["actor"]["enum"] == ["rift-player-character"]
    assert "actor_state" not in schema["properties"]
    assert "ability" not in schema["properties"]
    assert "proficiency_factor" not in schema["properties"]
    assert derivations["ability"]["mapping"]["perception"] == "wisdom"
    assert derivations["proficiency_factor"]["field"] == "skill_proficiencies"
    runtime = yaml.safe_load((ruleset_root / "runtime" / "ruleset.yaml").read_text("utf-8"))
    runtime_tools = runtime["tools"]
    d20_description = runtime_tools["d20_test"]["description"]
    assert "only when success or failure is genuinely uncertain" in d20_description
    assert "a general look around is not by itself a Perception check" in d20_description
    guide = " ".join(
        (ruleset_root / "prompts" / "gm-operating-guide.md").read_text("utf-8").split()
    )
    assert "Do not narrate unestablished training or past experience into existence" in guide
    assert "absence of proficiency is not automatic failure" in guide
    assert "show the limited result without inventing competence" in guide


def test_srd_combat_start_contract_makes_declared_hostility_engine_owned() -> None:
    ruleset_root = Path(__file__).parents[1] / "rulesets" / "srd-lite"
    guide = " ".join(
        (ruleset_root / "prompts" / "gm-operating-guide.md").read_text("utf-8").split()
    )
    runtime = yaml.safe_load((ruleset_root / "runtime" / "ruleset.yaml").read_text("utf-8"))
    runtime_tools = runtime["tools"]
    start_description = runtime_tools["start_combat_encounter_entities"]["description"]

    assert "Availability alone does not require combat" in guide
    assert "A declared hostile attack against a present creature establishes this condition" in (
        guide
    )
    assert "including when the target is a companion" in guide
    assert "Do not decide in prose that the attack misses, is deflected, or is de-escalated" in (
        guide
    )
    assert "including a companion" in start_description
    assert "Do not decide in prose that the attack misses, is deflected, or is de-escalated" in (
        start_description
    )
    assert runtime_tools["start_combat_encounter_entities"]["metadata"][
        "model_behavior"
    ] == "leaves_player_intent_pending"
    assert runtime_tools["start_group_combat_encounter"]["metadata"][
        "model_behavior"
    ] == "leaves_player_intent_pending"
    assert runtime_tools["resolve_combat_attack"]["metadata"][
        "model_behavior"
    ] == "resolves_player_intent"


@pytest.mark.parametrize(
    ("inventory", "followup_available"),
    ((["shortsword"], False), (["shortsword", "dagger"], True)),
)
def test_srd_light_attack_exposes_a_followup_only_with_a_different_light_weapon(
    inventory: list[str], followup_available: bool
) -> None:
    ruleset_root = Path(__file__).parents[1] / "rulesets" / "srd-lite"
    record = json.loads((ruleset_root / "package-record.json").read_text(encoding="utf-8"))
    declared = next(tool for tool in record["tools"] if tool["name"] == "resolve_combat_attack")
    declaration = ToolDeclaration(
        name=declared["name"],
        capability=declared["capability"],
        input_schema=owned_payload(declared["input_schema"]),
        state_predicates=(),
        turn_statuses=tuple(declared["turn_statuses"]),
        writers=tuple(declared["writers"]),
    )
    mechanic = materialize_package_mechanic(
        package_id="sylvia.srd-lite.bg3",
        package_version=record["version"],
        package_sha256="a" * 64,
        declaration=declaration,
        runtime_bytes=(ruleset_root / "runtime" / "ruleset.yaml").read_bytes(),
    )
    actor_id = "light-weapon-actor"
    target_id = "light-weapon-target"
    outcome = mechanic(
        MechanicContext(
            campaign_id="campaign-light-followup",
            turn_id="turn-light-followup",
            action_id=f"action-light-followup-{len(inventory)}",
            state=owned_payload(
                {
                    "sylvia.srd-lite.bg3.combat": {
                        "turn_active": True,
                        "active_actor": actor_id,
                        "action_available": True,
                        "light_attack_followup_available": False,
                    },
                    "sylvia.srd-lite.bg3.entities": {
                        actor_id: {
                            "hp": 10,
                            "max_hp": 10,
                            "is_alive": True,
                            "conditions": [],
                            "inventory": inventory,
                            "bonus_action_available": True,
                            "light_extra_attack_available": False,
                            "light_attack_source_weapon": None,
                            "help_attack_target": None,
                        },
                        target_id: {
                            "hp": 10,
                            "max_hp": 10,
                            "temp_hp": 0,
                            "armor_class": 100,
                            "is_alive": True,
                            "conditions": [],
                        },
                    },
                }
            ),
            parameters=owned_payload(
                {
                    "actor": actor_id,
                    "target": target_id,
                    "attack_sequence": "primary",
                    "use_graze_on_miss": False,
                    "weapon": "shortsword",
                    "attack_mode": "melee",
                    "attack_ability": "dexterity",
                    "wielding_two_handed": False,
                    "mounted": False,
                    "circumstantial_modifier": 0,
                    "advantage": False,
                    "disadvantage": False,
                    "actor_can_see_target": True,
                    "target_can_see_actor": True,
                    "target_is_charmer": False,
                    "fear_source_in_sight": False,
                    "attack_distance": 5,
                }
            ),
            participants=owned_payload(
                {
                    actor_id: {
                        "participant_id": actor_id,
                        "kind": "player_character",
                        "package_id": "sylvia.srd-lite.bg3",
                        "validation_status": "validated",
                        "data": {
                            "level": 1,
                            "abilities": {"dexterity": 16},
                            "weapon_proficiencies": ["martial_weapons"],
                            "weapon_masteries": [],
                        },
                    }
                }
            ),
        )
    )

    combat = next(mutation.value for mutation in outcome.mutations if mutation.key == "combat")
    assert combat["light_attack_followup_available"] is followup_available


def test_srd_creature_attack_binds_actor_and_guides_the_entity_target() -> None:
    record_path = Path(__file__).parents[1] / "rulesets" / "srd-lite" / "package-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in record["tools"]}
    properties = tools["resolve_adjudicated_creature_attack"]["input_schema"]["properties"]

    assert "actor" not in properties
    assert properties["target"]["x-sylvia-gm-reference"] == "entity"
    assert "Exact authoritative entity ID" in properties["target"]["description"]
    assert "exact enum value" in properties["damage_expression"]["description"]
    assert "unbound_d20_test" in tools


def test_srd_player_attack_binds_actor_state_from_the_active_combatant() -> None:
    record_path = Path(__file__).parents[1] / "rulesets" / "srd-lite" / "package-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in record["tools"]}
    properties = tools["resolve_combat_attack"]["input_schema"]["properties"]

    assert "actor_state" not in properties
    assert properties["actor"]["x-sylvia-gm-reference"] == "participant"
    assert properties["target"]["x-sylvia-gm-reference"] == "entity"
    assert "authoritative inventory" in properties["weapon"]["description"]
    assert "dart uses thrown" in properties["attack_mode"]["description"]
    assert "quarterstaff uses melee" in properties["attack_mode"]["description"]
    assert "dart uses Dexterity" in properties["attack_ability"]["description"]
    assert "quarterstaff uses Strength" in properties["attack_ability"]["description"]
    assert "For a dart this must be false" in properties["wielding_two_handed"]["description"]


def test_srd_combat_movement_binds_the_active_combatant_and_requires_distance() -> None:
    record_path = Path(__file__).parents[1] / "rulesets" / "srd-lite" / "package-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in record["tools"]}
    properties = tools["spend_combat_movement"]["input_schema"]["properties"]

    assert "actor" not in properties
    assert "actor_state" not in properties
    assert properties["distance"]["minimum"] == 1


def _archive(*, runtime_bytes: bytes | None = None) -> bytes:
    draft = json.loads((FIXTURE_ROOT / "package-record.json").read_text(encoding="utf-8"))
    relative_paths = (
        "content/content-declarations.json",
        "runtime/ruleset.yaml",
    )
    files = {path: (FIXTURE_ROOT / path).read_bytes() for path in relative_paths}
    if runtime_bytes is not None:
        files["runtime/ruleset.yaml"] = runtime_bytes
    record = {
        **draft,
        "files": [
            {
                "path": path,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
            for path, value in sorted(files.items())
        ],
    }
    record["content_sha256"] = content_root_for_record(record)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 3, "packages": [record]}),
        )
        for path, value in files.items():
            archive.writestr(f"packages/{PACKAGE_ID}/{path}", value)
    return stream.getvalue()


def _handler_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(set(value) & set(_EFFECT_HANDLERS))
        for child in value.values():
            found.update(_handler_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_handler_keys(child))
    return found


def _fixture_declarations() -> tuple[ToolDeclaration, ...]:
    draft = json.loads((FIXTURE_ROOT / "package-record.json").read_text(encoding="utf-8"))
    return tuple(
        ToolDeclaration(
            name=item["name"],
            capability=item["capability"],
            input_schema=item["input_schema"],
            state_predicates=(),
            turn_statuses=tuple(item["turn_statuses"]),
            writers=tuple(item["writers"]),
        )
        for item in draft["tools"]
    )


def test_ruleset_can_bind_a_non_choice_model_argument_from_authoritative_state() -> None:
    package_id = "sylvia-gm.conformance.state-bound"
    runtime_bytes = b"""sylvia_gm_runtime:
  format: 1
  state_namespace: sylvia-gm.conformance.state-bound
features: {}
abilities: {}
tools:
  close_current_window:
    description: Close the exact authoritative window.
    parameters:
      - name: actor
        type: string
        resolve: entity
        from_state: combat.current_actor
    pre_conditions:
      - condition: actor.name == 'Current Actor'
        error: The bound actor was not resolved.
    effects:
      - set: {field: state.closed_by, value: _actor_id}
definitions: {}
narrative: {}
"""
    declaration = ToolDeclaration(
        name="close_current_window",
        capability="state-bound.close",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        state_predicates=(),
        turn_statuses=("open",),
        writers=("dm",),
    )
    validate_package_runtime(
        package_id=package_id,
        declarations=(declaration,),
        runtime_bytes=runtime_bytes,
    )
    mechanic = materialize_package_mechanic(
        package_id=package_id,
        package_version="1.0.0",
        package_sha256="a" * 64,
        declaration=declaration,
        runtime_bytes=runtime_bytes,
    )

    outcome = mechanic(
        MechanicContext(
            campaign_id="campaign-state-bound",
            turn_id="turn-state-bound",
            action_id="action-state-bound",
            state=owned_payload(
                {
                    f"{package_id}.state": {"closed_by": None},
                    f"{package_id}.combat": {"current_actor": "actor-current"},
                    f"{package_id}.entities": {"actor-current": {"name": "Current Actor"}},
                }
            ),
            parameters=owned_payload({}),
        )
    )

    assert outcome.mutations == (
        StateMutation(package_id, "state", {"closed_by": "actor-current"}),
    )


@pytest.mark.parametrize("missing_bound", ("minimum", "maximum"))
def test_rule005_runtime_and_manifest_numeric_bounds_must_match(
    missing_bound: str,
) -> None:
    draft = json.loads((FIXTURE_ROOT / "package-record.json").read_text(encoding="utf-8"))
    runtime_bytes = (FIXTURE_ROOT / "runtime" / "ruleset.yaml").read_bytes()
    if missing_bound == "maximum":
        runtime_bytes = runtime_bytes.replace(
            b"      - name: stress\n        type: integer\n        minimum: 0\n",
            b"      - name: stress\n        type: integer\n"
            b"        minimum: 0\n        maximum: 10\n",
        )
    declarations = []
    for item in draft["tools"]:
        input_schema = json.loads(json.dumps(item["input_schema"]))
        if item["name"] == "apply_operation_cost":
            stress_schema = input_schema["properties"]["stress"]
            if missing_bound == "maximum":
                stress_schema["maximum"] = 10
            stress_schema.pop(missing_bound)
        declarations.append(
            ToolDeclaration(
                name=item["name"],
                capability=item["capability"],
                input_schema=input_schema,
                state_predicates=(),
                turn_statuses=tuple(item["turn_statuses"]),
                writers=tuple(item["writers"]),
            )
        )

    with pytest.raises(PackageRuntimeError, match="does not match"):
        validate_package_runtime(
            package_id=PACKAGE_ID,
            declarations=tuple(declarations),
            runtime_bytes=runtime_bytes,
        )


@pytest.mark.parametrize(
    ("replacement", "expected"),
    (
        (b"call: missing_program", "unknown tool"),
        (b"call: run_program", "static call cycle"),
    ),
)
def test_rule005_package_admission_rejects_unsafe_static_call_graphs(
    replacement: bytes, expected: str
) -> None:
    runtime_bytes = (
        (FIXTURE_ROOT / "runtime" / "ruleset.yaml")
        .read_bytes()
        .replace(b"call: commit_program", replacement)
    )

    with pytest.raises(PackageRuntimeError, match=expected):
        validate_package_runtime(
            package_id=PACKAGE_ID,
            declarations=_fixture_declarations(),
            runtime_bytes=runtime_bytes,
        )


def _command(
    *,
    owner_id: str,
    revision: int,
    suffix: str,
    tool_name: str,
    parameters: dict[str, object],
) -> ActionCommand:
    return ActionCommand(
        campaign_id=CAMPAIGN_ID,
        action_id=f"action-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        owner_id=owner_id,
        writer=WriterRole.DM,
        expected_revision=revision,
        turn_id=TURN_ID,
        package_id=PACKAGE_ID,
        tool_name=tool_name,
        parameters=parameters,
    )


def test_rule005_format3_package_executes_persists_reopens_and_replays(
    tmp_path: Path,
) -> None:
    runtime_bytes = (FIXTURE_ROOT / "runtime" / "ruleset.yaml").read_bytes()
    raw_runtime = yaml.safe_load(runtime_bytes)
    assert "abilities" not in raw_runtime
    assert "features" not in raw_runtime
    assert all("ability" not in skill for skill in raw_runtime["skills"])
    used_handlers = _handler_keys(raw_runtime)
    assert used_handlers <= _NEUTRAL_EFFECT_KEYS
    assert not (used_handlers & _COMPATIBILITY_EFFECT_KEYS)

    database = tmp_path / "rule005-authority.db"
    with AuthorityOwner.open(database, "owner-rule005-a") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_archive()),
            install_id="install-rule005-signal-static",
        )
        assert candidate.package_lock.entries[0].package_id == PACKAGE_ID
        owner.create_campaign(
            CampaignCreate(
                campaign_id=CAMPAIGN_ID,
                display_name="RULE-005 Signal and Static",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id=TURN_ID,
                initial_state=(
                    StateValue(
                        PACKAGE_ID,
                        "state",
                        {
                            "stress": 0,
                            "wounds": 0,
                            "resources": {"charge": 5},
                            "conditions": {"interference": False},
                            "advancement": {"intrusion": 0},
                            "last_check": {
                                "skill": None,
                                "total": 0,
                                "outcome": None,
                            },
                            "last_program": {"name": None, "result": 0},
                            "active_actor": "signal-operator",
                            "entities": {
                                "signal-operator": {
                                    "name": "Relay Operator",
                                    "stress": 0,
                                }
                            },
                        },
                    ),
                ),
            )
        )
        assert tuple(tool.name for tool in owner.catalog(CAMPAIGN_ID)) == (
            "apply_operation_cost",
            "resolve_intrusion",
            "run_program",
        )
        boundary = owner.model_decision_boundary(CAMPAIGN_ID)
        assert boundary is not None
        assert boundary.owner is ModelDecisionOwner.GM
        assert boundary.actor_id == "signal-operator"
        assert boundary.actor_name == "Relay Operator"
        assert boundary.actor_state["stress"] == 0
        assert owner.ruleset_gm_operating_guide(CAMPAIGN_ID) is None
        surface = owner.campaign_panels(CAMPAIGN_ID)["surfaces"][0]
        assert surface["id"] == "signal-static-status"
        assert surface["renderer"] == "key_value"
        assert surface["actions"] == [
            {
                "id": "boost-signal",
                "label": "Boost signal",
                "intent": "run_program",
                "params": {"program": "signal_boost", "amount": 1},
            }
        ]
        mechanics = StatelessMcpMechanics(owner)
        assert tuple(tool.name for tool in mechanics.list_tools(CAMPAIGN_ID)) == (
            "apply_operation_cost",
            "resolve_intrusion",
            "run_program",
        )
        with pytest.raises(MechanicsRejected) as rejected:
            mechanics.execute_action(
                _command(
                    owner_id="owner-rule005-a",
                    revision=0,
                    suffix="invalid-resolve",
                    tool_name="resolve_intrusion",
                    parameters={"skill": "intrusion"},
                )
            )
        assert rejected.value.code == "mechanics_validation_failed"
        assert rejected.value.field_paths == ("pressure",)
        assert [issue.as_payload() for issue in rejected.value.validation_issues] == [
            {
                "path": "pressure",
                "kind": "missing_required_field",
                "expected_type": "integer",
                "parent_path": "$",
            }
        ]
        assert owner.snapshot(CAMPAIGN_ID).revision == 0
        safe_diagnostic = owner.admin_full_audit_projection()["safe_mechanics_diagnostics"][-1]
        assert safe_diagnostic == {
            "kind": "mechanics_rejection",
            "privacy": "safe_metadata_only",
            "session_id": None,
            "turn_id": TURN_ID,
            "action_id": "action-invalid-resolve",
            "tool_name": "resolve_intrusion",
            "rejection_code": "mechanics_validation_failed",
            "validation_field_paths": ["pressure"],
            "state_changed": False,
        }
        check = mechanics.execute_action(
            _command(
                owner_id="owner-rule005-a",
                revision=0,
                suffix="resolve",
                tool_name="resolve_intrusion",
                parameters={"skill": "intrusion", "pressure": 7},
            )
        )
        assert check.status is ActionStatus.COMMITTED
        first_state = owner.snapshot(CAMPAIGN_ID).state[f"{PACKAGE_ID}.state"]
        assert first_state["last_check"]["skill"] == "intrusion"
        assert 2 <= first_state["last_check"]["total"] <= 12
        assert first_state["last_check"]["outcome"] in {"clear", "compromised"}

    with AuthorityOwner.open(database, "owner-rule005-b") as reopened:
        assert reopened.snapshot(CAMPAIGN_ID).revision == 1
        program_command = _command(
            owner_id="owner-rule005-b",
            revision=1,
            suffix="program",
            tool_name="run_program",
            parameters={"program": "signal_boost", "amount": 4},
        )
        program = StatelessMcpMechanics(reopened).execute_action(program_command)
        program_replay = StatelessMcpMechanics(reopened).execute_action(program_command)
        assert program.status is ActionStatus.COMMITTED
        assert program_replay.status is ActionStatus.COMMITTED
        assert program_replay.resulting_revision == program.resulting_revision == 2
        assert program.result["context"]["_definition_key"] == "signal_boost"
        assert program.result["context"]["_definition_table"] == "programs"
        assert program.result["context"]["_definition"] == {
            "charge_cost": 2,
            "family": "add_stress",
            "multiplier": 3,
        }
        cost_command = _command(
            owner_id="owner-rule005-b",
            revision=2,
            suffix="cost",
            tool_name="apply_operation_cost",
            parameters={"stress": 2, "wounds": 1, "charge_spent": 2, "progress": 3},
        )
        cost = reopened.execute_action(cost_command)
        replay = reopened.execute_action(cost_command)
        assert cost.status is ActionStatus.COMMITTED
        assert replay.status is ActionStatus.COMMITTED
        assert replay.resulting_revision == cost.resulting_revision == 3
        state = reopened.snapshot(CAMPAIGN_ID).state[f"{PACKAGE_ID}.state"]
        assert state["stress"] == 3
        assert state["wounds"] == 1
        assert state["resources"]["charge"] == 1
        assert state["conditions"]["interference"] is True
        assert state["advancement"]["intrusion"] == 3
        assert state["last_program"] == {"name": "signal_boost", "result": 12}

    with AuthorityOwner.open(database, "owner-rule005-c") as final:
        snapshot = final.snapshot(CAMPAIGN_ID)
        assert snapshot.revision == 3
        assert snapshot.state[f"{PACKAGE_ID}.state"]["stress"] == 3
        print(
            "RULE-005 FORMAT-3",
            candidate.package_lock.entries[0].sha256,
            sorted(used_handlers),
            "revision",
            snapshot.revision,
            "replay",
            replay.resulting_revision,
            "program replay",
            program_replay.resulting_revision,
        )


def test_rule005_package_precondition_rejection_reaches_the_gm_as_actionable_feedback(
    tmp_path: Path,
) -> None:
    runtime = yaml.safe_load(
        (FIXTURE_ROOT / "runtime" / "ruleset.yaml").read_text(encoding="utf-8")
    )
    runtime["tools"]["apply_operation_cost"]["pre_conditions"] = [
        {
            "condition": "stress <= 1",
            "error": "Stress cost cannot exceed one for this operation.",
        }
    ]
    runtime_bytes = yaml.safe_dump(runtime, sort_keys=False).encode("utf-8")
    database = tmp_path / "ruleset-precondition.db"

    with AuthorityOwner.open(database, "owner-rule005-precondition") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_archive(runtime_bytes=runtime_bytes)),
            install_id="install-rule005-precondition",
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id=CAMPAIGN_ID,
                display_name="RULE-005 precondition feedback",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id=TURN_ID,
                initial_state=(
                    StateValue(
                        PACKAGE_ID,
                        "state",
                        {
                            "stress": 0,
                            "wounds": 0,
                            "resources": {"charge": 5},
                            "conditions": {"interference": False},
                            "advancement": {"intrusion": 0},
                        },
                    ),
                ),
            )
        )

        with pytest.raises(MechanicsRejected) as rejected:
            StatelessMcpMechanics(owner).execute_action(
                _command(
                    owner_id="owner-rule005-precondition",
                    revision=0,
                    suffix="precondition",
                    tool_name="apply_operation_cost",
                    parameters={
                        "stress": 2,
                        "wounds": 0,
                        "charge_spent": 0,
                        "progress": 0,
                    },
                )
            )

        assert rejected.value.code == "ruleset_precondition_failed"
        assert str(rejected.value) == "Stress cost cannot exceed one for this operation."
        assert rejected.value.state_changed is False
        assert owner.snapshot(CAMPAIGN_ID).revision == 0
