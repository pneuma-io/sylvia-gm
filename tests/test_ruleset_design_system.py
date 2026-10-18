"""Focused Ruleset-authoring and installed-inspection contract tests."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    parse_gameplay_support_bytes,
)

SRD_ROOT = Path(__file__).parents[1] / "rulesets" / "srd-lite"


def _support_record(
    identity: str,
    resolution: str,
    *,
    reference: str = "included",
    family: str | None = None,
    deterministic_part: str | None = None,
    gm_part: str | None = None,
) -> dict[str, object]:
    return {
        "id": identity,
        "reference_availability": reference,
        "gameplay_resolution": resolution,
        "mechanic_family": family,
        "deterministic_part": deterministic_part,
        "gm_part": gm_part,
    }


def test_gameplay_support_uses_installed_resolution_claims_not_progress_labels() -> None:
    records = [
        _support_record("family", "engine_family", family="test-family"),
        _support_record(
            "hybrid",
            "hybrid",
            family="test-hybrid",
            deterministic_part="roll",
            gm_part="fictional position",
        ),
        _support_record("narrative", "gm_adjudicated", family="bounded-adjudication"),
        _support_record("none", "not_applicable"),
        _support_record("special", "engine_specialized"),
        _support_record("unsupported", "unsupported", reference="excluded"),
    ]
    support = parse_gameplay_support_bytes(
        canonical_json_bytes(
            {
                "format_id": "sylvia-gm-gameplay-support",
                "format_version": 1,
                "records": sorted(records, key=lambda item: str(item["id"])),
            }
        )
    )

    assert {item.gameplay_resolution for item in support.records} == {
        "not_applicable",
        "engine_family",
        "engine_specialized",
        "hybrid",
        "gm_adjudicated",
        "unsupported",
    }

    invalid = [
        _support_record("bad", "pending"),
        _support_record("bad", "gm_adjudicated", reference="excluded", family="gm"),
    ]
    for record in invalid:
        with pytest.raises(ValueError):
            parse_gameplay_support_bytes(
                canonical_json_bytes(
                    {
                        "format_id": "sylvia-gm-gameplay-support",
                        "format_version": 1,
                        "records": [record],
                    }
                )
            )


def test_checked_in_srd_has_one_canonical_group_order_gate() -> None:
    """Repeated generation must not append another legacy/group Initiative gate."""

    record = json.loads((SRD_ROOT / "package-record.json").read_text(encoding="utf-8"))
    advance = next(
        declaration
        for declaration in record["tools"]
        if declaration["name"] == "advance_combat_encounter_turn"
    )
    group_order_gates = [
        predicate
        for predicate in advance["state_predicates"]
        if predicate.get("namespace") == "sylvia.srd-lite.bg3"
        and predicate.get("key") == "combat.group_order"
    ]
    assert group_order_gates == [
        {
            "namespace": "sylvia.srd-lite.bg3",
            "key": "combat.group_order",
            "equals": True,
            "negated": True,
        }
    ]


def test_compact_source_expands_a_family_and_keeps_private_mechanics_private() -> None:
    source = b"""format: 1
id: example.family-rules
version: 1.0.0
label: Family Rules
publisher: Example author
mechanic_families:
  spend:
    parameters:
      - name: actor
        type: entity_id
        source: state
        from_state: combat.active_actor
        resolve: entity
      - {name: amount, type: integer, source: choice, minimum: 1, maximum: 3}
      - {name: fictional_cost, type: boolean, source: gm_judgment}
      - name: allies
        type: entity_id_list
        source: choice
        resolve: entity_list
        min_items: 1
        unique_items: true
    pre_conditions: []
    effects:
      - decrement: {field: "state.resources.{resource}", amount: amount}
    narrative: {}
public_actions:
  - name: spend_luck
    family: spend
    description: Spend bounded luck.
    configure: {resource: luck}
    capability: example.family-rules.luck
    state_predicates: []
    turn_statuses: [open]
    writers: [dm]
    reference_availability: included
    gameplay_resolution: hybrid
    deterministic_part: Bounded resource mutation.
    gm_part: Whether the fictional cost applies.
private_mechanics:
  - name: spend_private
    family: spend
    description: Private family reuse.
    configure: {resource: reserve}
definitions: {starting_luck: 3}
participant_artifacts: []
operating_guide: null
"""

    compiled = compile_ruleset_source(source)
    with materialize_package_archive((compiled.draft,)) as first:
        first_bytes = first.archive.read()
        manifest = first.manifest
    with materialize_package_archive((compile_ruleset_source(source).draft,)) as second:
        assert second.archive.read() == first_bytes

    assert [tool.name for tool in manifest.tools] == ["spend_luck"]
    schema = manifest.tools[0].input_schema
    assert set(schema["properties"]) == {"allies", "amount", "fictional_cost"}
    assert schema["properties"]["fictional_cost"]["x-sylvia-gm-input-source"] == ("gm_judgment")
    assert schema["properties"]["allies"]["items"]["x-sylvia-gm-reference"] == ("entity")
    runtime = yaml.safe_load(compiled.draft.payloads["runtime/ruleset.yaml"])
    assert set(runtime["tools"]) == {"spend_luck", "spend_private"}
    assert runtime["tools"]["spend_luck"]["parameters"][0]["from_state"] == ("combat.active_actor")


@pytest.mark.parametrize(
    ("effect", "expected_features"),
    (
        (
            """      - spatial_query:
          scene: state.spatial_scene
          operation: snapshot
        store_as: _firing_scene
      - shot_effect:
          scene: state.spatial_scene
          snapshot: '{_firing_scene}'
          source_id: shooter
          target_id: target
          origin: [1, 1, 1]
          destination: [4, 1, 1]
          delay_ms: 0
          miss: true
          contacts: []""",
            ("shot_effects.v1", "tactical_grid.v1"),
        ),
        (
            """      - spatial_mutation:
          scene: state.spatial_scene
          map_id: arena
          expected_revision: 4
          target: {kind: geometry, id: door}
          patch: {active: false}
        store_as: _mutation""",
            ("spatial_mutation.v1", "tactical_grid.v1"),
        ),
        (
            """      - spatial_mutation:
          scene: state.spatial_scene
          map_id: arena
          expected_revision: 4
          target: {kind: geometry, id: door}
          patch: {occupies_cells: false}
        store_as: _mutation""",
            ("spatial_clearance.v1", "spatial_mutation.v1", "tactical_grid.v1"),
        ),
        (
            """      - spatial_query:
          scene: state.spatial_scene
          operation: path
          query:
            map_id: arena
            start: [1, 1]
            goal: [4, 1]
            allow_diagonal: false
            corner_policy: forbid_cutting
            include_entities: true
            moving_entity_id: mover
            max_steps: 64
        store_as: _path""",
            ("spatial_clearance.v1", "spatial_path_moving_entity.v1", "tactical_grid.v1"),
        ),
    ),
)
def test_compact_source_declares_each_spatial_runtime_capability(
    effect: str,
    expected_features: tuple[str, ...],
) -> None:
    source = f"""format: 1
id: example.spatial-rules
version: 1.0.0
label: Spatial Rules
publisher: Example author
mechanic_families:
  spatial:
    parameters: []
    pre_conditions: []
    effects:
{effect}
    narrative: {{}}
public_actions:
  - name: change_scene
    family: spatial
    description: Exercise one shared spatial primitive.
    configure: {{}}
    capability: example.spatial-rules.change
    state_predicates: []
    turn_statuses: [open]
    writers: [dm]
    reference_availability: included
    gameplay_resolution: engine_family
    deterministic_part: null
    gm_part: null
private_mechanics: []
definitions: {{}}
participant_artifacts: []
operating_guide: null
""".encode()

    compiled = compile_ruleset_source(source)

    assert compiled.required_engine_features == expected_features
    assert compiled.draft.record["compatibility"]["requires"] == list(expected_features)


def test_compact_source_returns_actionable_source_diagnostic() -> None:
    with pytest.raises(CommandRejected) as raised:
        compile_ruleset_source(b"format: 1\nid: incomplete\n")

    diagnostics = [item.as_payload() for item in raised.value.diagnostics]
    assert {item["path"] for item in diagnostics} == {
        "$.version", "$.label", "$.publisher", "$.mechanic_families", "$.public_actions",
        "$.private_mechanics", "$.definitions", "$.participant_artifacts", "$.operating_guide",
    }
    assert all(item["code"] == "ruleset_source_missing_field" for item in diagnostics)
    assert all(item["file"] == "ruleset-source.yaml" for item in diagnostics)
    assert all(item["package_id"] == "incomplete" for item in diagnostics)


def _installed_source(version: str, description: str) -> bytes:
    return f"""format: 1
id: example.inspectable-rules
version: {version}
label: Inspectable Rules
publisher: Example author
mechanic_families:
  note:
    parameters:
      - {{name: detail, type: string, source: gm_judgment}}
    pre_conditions: []
    effects: []
    narrative: {{}}
public_actions:
  - name: record_detail
    family: note
    description: {description}
    configure: {{}}
    capability: example.inspectable-rules.record
    state_predicates: []
    turn_statuses: [open]
    writers: [dm]
    reference_availability: included
    gameplay_resolution: gm_adjudicated
    deterministic_part: null
    gm_part: null
private_mechanics:
  - name: internal_note
    family: note
    description: Private family reuse.
    configure: {{}}
definitions: {{}}
participant_artifacts: []
operating_guide: Inspect the installed rules safely.
""".encode()


def _archive_for_source(source: bytes) -> bytes:
    compiled = compile_ruleset_source(source)
    with materialize_package_archive((compiled.draft,)) as materialized:
        return materialized.archive.read()


@pytest.mark.parametrize("case", ["field", "unused_family", "duplicate_operation", "version_type"])
def test_compact_source_rejects_ambiguous_or_undeclared_authoring(case: str) -> None:
    source = yaml.safe_load(_installed_source("1.0.0", "Record a detail."))
    if case == "field":
        source["settings"] = []
        expected_path = "$.settings"
    elif case == "unused_family":
        source["mechanic_families"]["unused"] = {
            "parameters": [], "pre_conditions": [], "effects": [], "narrative": {},
            "invented_field": True,
        }
        expected_path = "$.mechanic_families.unused.invented_field"
    elif case == "duplicate_operation":
        source["public_actions"][0]["name"] = source["private_mechanics"][0]["name"]
        expected_path = "$.public_actions[0].name"
    else:
        source["format"] = True
        expected_path = "$.format"

    with pytest.raises(CommandRejected) as raised:
        compile_ruleset_source(yaml.safe_dump(source).encode("utf-8"))
    assert raised.value.diagnostics[0].path == expected_path


def test_installed_rulesets_are_browsable_exportable_and_comparable(
    tmp_path: Path,
) -> None:
    first_archive = _archive_for_source(
        _installed_source("1.0.0", "Record a bounded detail."),
    )
    second_archive = _archive_for_source(
        _installed_source("1.1.0", "Record one revised bounded detail."),
    )
    database = tmp_path / "authority.db"
    with AuthorityOwner.open(database, "isolated-ruleset-inspector") as owner:
        first = owner.install_package_archive(io.BytesIO(first_archive), install_id="first")
        second = owner.install_package_archive(io.BytesIO(second_archive), install_id="second")

        inspected = owner.inspect_package_candidate(first.candidate_id)
        package = inspected["packages"][0]
        assert package["id"] == "example.inspectable-rules"
        assert package["version"] == "1.0.0"
        assert [item["name"] for item in package["public_actions"]] == ["record_detail"]
        assert package["private_mechanics"] == ["internal_note"]
        assert package["support_counts"] == {"gm_adjudicated": 1}
        assert {item["path"] for item in package["files"]} >= {
            "runtime/ruleset.yaml",
            "support/gameplay-resolution.json",
        }

        preview = owner.package_candidate_text(
            first.candidate_id,
            "example.inspectable-rules",
            "runtime/ruleset.yaml",
        )
        assert preview["content"].startswith("sylvia_gm_runtime:")
        assert preview["sha256"] == hashlib.sha256(preview["content"].encode("utf-8")).hexdigest()

        exported = io.BytesIO()
        result = owner.export_package_candidate(first.candidate_id, exported)
        assert result["archive_sha256"] == hashlib.sha256(exported.getvalue()).hexdigest()
        assert result["byte_count"] == len(exported.getvalue())

        comparison = owner.compare_package_candidates(
            first.candidate_id,
            second.candidate_id,
        )
        assert comparison["same_closure"] is False
        assert comparison["changes"] == [
            {
                "package_id": "example.inspectable-rules",
                "change": "changed",
                "left_version": "1.0.0",
                "right_version": "1.1.0",
                "left_content_sha256": first.package_lock.entries[0].sha256,
                "right_content_sha256": second.package_lock.entries[0].sha256,
                "changed_files": [
                    "content/content-declarations.json",
                    "runtime/ruleset.yaml",
                ],
            }
        ]

    with AuthorityOwner.open(tmp_path / "reinstalled.db", "isolated-reinstall") as owner:
        reinstalled = owner.install_package_archive(
            io.BytesIO(exported.getvalue()),
            install_id="exported",
        )
        assert reinstalled.package_lock == first.package_lock
        assert reinstalled.closure_sha256 == first.closure_sha256


def test_opportunity_reaction_binds_the_triggering_entities_before_resolution() -> None:
    record = json.loads((SRD_ROOT / "package-record.json").read_text(encoding="utf-8"))
    manifest_tools = {item["name"]: item for item in record["tools"]}
    runtime = yaml.safe_load((SRD_ROOT / "runtime" / "ruleset.yaml").read_text(encoding="utf-8"))

    trigger_schema = manifest_tools["trigger_opportunity_attack"]["input_schema"]
    assert set(trigger_schema["properties"]) == {
        "reactor_state",
        "actor_can_see_target",
        "target_can_see_actor",
        "attack_distance",
        "attack_modifier",
        "damage_dice_count",
        "damage_die_size",
        "damage_modifier",
        "damage_type",
    }
    trigger_parameters = {
        item["name"]: item for item in runtime["tools"]["trigger_opportunity_attack"]["parameters"]
    }
    assert trigger_parameters["provoking_state"]["from_state"] == "combat.active_actor"

    resolution_schema = manifest_tools["resolve_opportunity_attack"]["input_schema"]
    assert set(resolution_schema["properties"]) == {
        "target_is_charmer",
        "fear_source_in_sight",
        "advantage",
        "disadvantage",
    }
    resolution_parameters = {
        item["name"]: item for item in runtime["tools"]["resolve_opportunity_attack"]["parameters"]
    }
    assert resolution_parameters["actor_state"]["from_state"] == (
        "combat.pending_reaction.reacting_entity"
    )
    assert resolution_parameters["target_state"]["from_state"] == (
        "combat.pending_reaction.provoking_entity"
    )


def test_opportunity_reaction_executes_from_its_immutable_pending_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sylvia_gm import dice

    # This journey needs a hit; package-content changes otherwise can seed a natural-one miss.
    set_seed = dice.set_seed
    monkeypatch.setattr(dice, "set_seed", lambda _seed: set_seed(0))
    package_id = "sylvia.srd-lite.bg3"
    with AuthorityOwner.open(tmp_path / "reaction.db", "reaction-owner") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()),
            install_id="reaction-rules",
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="reaction-campaign",
                display_name="Synthetic reaction",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="reaction-turn",
                initial_state=(
                    StateValue(
                        package_id,
                        "combat",
                        {
                            "phase": "turn_active",
                            "turn_active": True,
                            "active_actor": "mover",
                            "opportunity_attack_pending": False,
                            "pending_reaction": None,
                            "disengaged_actor": None,
                        },
                    ),
                    StateValue(
                        package_id,
                        "entities",
                        {
                            "reactor": {
                                "is_alive": True,
                                "reaction_available": True,
                                "available_reactions": ["opportunity_attack"],
                                "melee_reach": 5,
                                "conditions": [],
                                "help_attack_target": None,
                                "concentration_d20_modifier_source_ids": None,
                                "hidden": False,
                            },
                            "mover": {
                                "is_alive": True,
                                "hp": 12,
                                "max_hp": 12,
                                "temp_hp": 0,
                                "armor_class": 10,
                                "creature_kind": "monster",
                                "conditions": [],
                                "dodging": False,
                                "speed": 30,
                                "exhaustion_level": 0,
                                "damage_immunities": [],
                                "damage_resistances": [],
                                "damage_vulnerabilities": [],
                                "concentration_spell_id": None,
                            },
                        },
                    ),
                ),
            )
        )

        def command(
            revision: int, action_id: str, tool: str, parameters: dict[str, object]
        ) -> ActionCommand:
            return ActionCommand(
                campaign_id="reaction-campaign",
                action_id=action_id,
                idempotency_key=f"idempotency-{action_id}",
                owner_id="reaction-owner",
                writer=WriterRole.DM,
                expected_revision=revision,
                turn_id="reaction-turn",
                package_id=package_id,
                tool_name=tool,
                parameters=parameters,
            )

        triggered = owner.execute_action(
            command(
                0,
                "trigger",
                "trigger_opportunity_attack",
                {
                    "reactor_state": "reactor",
                    "actor_can_see_target": True,
                    "target_can_see_actor": True,
                    "attack_distance": 5,
                    "attack_modifier": 100,
                    "damage_dice_count": 1,
                    "damage_die_size": "4",
                    "damage_modifier": 0,
                    "damage_type": "slashing",
                },
            )
        )
        assert triggered.status is ActionStatus.COMMITTED, triggered.result
        pending = owner.snapshot("reaction-campaign").state[f"{package_id}.combat"][
            "pending_reaction"
        ]
        assert pending["reacting_entity"] == "reactor"
        assert pending["provoking_entity"] == "mover"

        resolved = owner.execute_action(
            command(
                1,
                "resolve",
                "resolve_opportunity_attack",
                {
                    "target_is_charmer": False,
                    "fear_source_in_sight": False,
                    "advantage": False,
                    "disadvantage": False,
                },
            )
        )
        assert resolved.status is ActionStatus.COMMITTED, resolved.result
        snapshot = owner.snapshot("reaction-campaign").state
        assert snapshot[f"{package_id}.combat"]["pending_reaction"] is None
        assert snapshot[f"{package_id}.entities"]["reactor"]["reaction_available"] is False
        assert snapshot[f"{package_id}.entities"]["mover"]["hp"] < 12


def test_group_encounter_keeps_a_companion_in_the_initiative_cycle(tmp_path: Path) -> None:
    package_id = "sylvia.srd-lite.bg3"
    entity = {
        "hp": 10,
        "max_hp": 10,
        "is_alive": True,
        "initiative_modifier": 0,
        "speed": 30,
        "creature_kind": "character",
        "conditions": [],
        "exhaustion_level": 0,
        "helped_ally_state_id": None,
        "prepared_spells": [],
        "available_bonus_actions": [],
    }
    with AuthorityOwner.open(tmp_path / "group.db", "group-owner") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="group-rules"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="group-campaign",
                display_name="Synthetic group encounter",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="group-turn",
                initial_state=(
                    StateValue(package_id, "combat", {"encounter_active": False}),
                    StateValue(
                        package_id,
                        "entities",
                        {
                            "player": {**entity, "initiative_modifier": -10},
                            "companion": {**entity, "initiative_modifier": 20},
                            "hostile": {
                                **entity,
                                "creature_kind": "monster",
                                "initiative_modifier": -10,
                            },
                        },
                    ),
                ),
            )
        )

        def command(
            revision: int,
            action_id: str,
            tool: str,
            parameters: dict[str, object],
        ) -> ActionCommand:
            return ActionCommand(
                campaign_id="group-campaign",
                action_id=action_id,
                idempotency_key=f"idempotency-{action_id}",
                owner_id="group-owner",
                writer=WriterRole.DM,
                expected_revision=revision,
                turn_id="group-turn",
                package_id=package_id,
                tool_name=tool,
                parameters=parameters,
            )

        started = owner.execute_action(
            command(
                0,
                "start-group",
                "start_group_combat_encounter",
                {"combatants": ["player", "companion", "hostile"]},
            )
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        assert (
            owner.snapshot("group-campaign").state[f"{package_id}.combat"]["current_actor"]
            == "companion"
        )
        visited: list[str] = []
        revision = 1
        for index in range(3):
            combat = owner.snapshot("group-campaign").state[f"{package_id}.combat"]
            visited.append(combat["current_actor"])
            begun = owner.execute_action(
                command(revision, f"begin-{index}", "begin_combat_turn", {})
            )
            assert begun.status is ActionStatus.COMMITTED, begun.result
            revision += 1
            ended = owner.execute_action(command(revision, f"end-{index}", "end_combat_turn", {}))
            assert ended.status is ActionStatus.COMMITTED, ended.result
            revision += 1
            if index < 2:
                advanced = owner.execute_action(
                    command(
                        revision,
                        f"advance-{index}",
                        "advance_group_combat_encounter_turn",
                        {},
                    )
                )
                assert advanced.status is ActionStatus.COMMITTED, advanced.result
                revision += 1
        assert set(visited) == {"player", "companion", "hostile"}


def test_short_rest_hit_die_spends_the_resource_and_heals(tmp_path: Path) -> None:
    package_id = "sylvia.srd-lite.bg3"
    with AuthorityOwner.open(tmp_path / "rest.db", "rest-owner") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="rest-rules"
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="rest-campaign",
                display_name="Synthetic short rest",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="rest-turn",
                initial_state=(
                    StateValue(package_id, "combat", {"encounter_active": False}),
                    StateValue(
                        package_id,
                        "entities",
                        {
                            "hero": {
                                "hp": 3,
                                "max_hp": 10,
                                "is_alive": True,
                                "hit_die_size": 8,
                                "constitution_modifier": 1,
                                "resources": {"hit_die": 1},
                            }
                        },
                    ),
                ),
            )
        )
        for tool, parameters in [
            ("begin_srd_rest", {"target_state": "hero", "kind": "short", "activity": "light"}),
            ("advance_srd_lasting_time", {"elapsed_rounds": 600}),
            ("complete_srd_rest", {"target_state": "hero"}),
        ]:
            revision = owner.snapshot("rest-campaign").revision
            rest = owner.execute_action(ActionCommand(
                campaign_id="rest-campaign", action_id=f"rest-{revision}",
                idempotency_key=f"rest-{revision}", owner_id="rest-owner", writer=WriterRole.DM,
                expected_revision=revision, turn_id="rest-turn", package_id=package_id,
                tool_name=tool, parameters=parameters,
            ))
            assert rest.status is ActionStatus.COMMITTED, rest.result
        result = owner.execute_action(
            ActionCommand(
                campaign_id="rest-campaign",
                action_id="short-rest",
                idempotency_key="idempotency-short-rest",
                owner_id="rest-owner",
                writer=WriterRole.DM,
                expected_revision=owner.snapshot("rest-campaign").revision,
                turn_id="rest-turn",
                package_id=package_id,
                tool_name="take_srd_short_rest_hit_die",
                parameters={"target_state": "hero"},
            )
        )
        assert result.status is ActionStatus.COMMITTED, result.result
        hero = owner.snapshot("rest-campaign").state[f"{package_id}.entities"]["hero"]
        assert hero["resources"]["hit_die"] == 0
        assert hero["hp"] > 3
