"""Earned awards and player-approved level-ups use the installed package and real authority."""

import hashlib
import io
import sqlite3
from dataclasses import replace

import pytest

from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEvent, SessionRunRequest
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.application.plan_state_admission import validate_plan_state
from sylvia_gm.domain.authority import (
    ActionStatus,
    AwaitingChoiceReceipt,
    MechanicOutcome,
    ParticipantMutation,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.campaign_plan import CAMPAIGN_PLAN_FORMAT_VERSION
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected, SchemaIdentityError
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ToolDef, ToolParameter
from tests.srd_full.test_srd_second_wind import PACKAGE_ID, _installed_fighter


def test_profile_writes_refresh_named_references_and_preserve_captured_before_values():
    bridge = AuthorityMemoryStateBridge(
        "example.rules",
        {
            "example.rules.entities": {"hero": {"hp": 7}},
        },
        {
            "hero": {
                "participant_id": "hero",
                "package_id": "example.rules",
                "data": {"level": 1},
            }
        },
    )
    tool = ToolDef(
        name="advance",
        parameters=[
            ToolParameter("character", "string", resolve="participant"),
            ToolParameter("character_state", "string", resolve="entity"),
        ],
        effects=[
            {"set": {"field": "_before", "value": "character.data"}},
            {"set_participant": {"participant": "_character_id", "field": "level", "value": 2}},
            {"set": {"field": "_after_level", "value": "character.data.level"}},
            {"set": {"field": "_hp", "value": "character_state.hp"}},
        ],
    )
    result = execute_tool(
        tool.name,
        {"character": "hero", "character_state": "hero"},
        {tool.name: tool},
        bridge,
        {},
        {},
        {},
    )
    assert result.success, result.error
    assert result.context["_before"] == {"level": 1}
    assert result.context["_after_level"] == 2
    assert result.context["_hp"] == 7
    assert bridge.participant_mutations() == (ParticipantMutation("hero", {"level": 2}),)


def create(
    owner,
    *,
    method="milestone",
    level=1,
    multiclass_ready=False,
    writer=WriterRole.DM,
    campaign_id="advancement",
    participant_overrides=None,
    entity_overrides=None,
):
    candidate = owner.install_package_archive(io.BytesIO(bundled_srd_archive()), install_id="srd")
    character = _installed_fighter(candidate.package_lock, fighter=True)
    character.data["level"] = level
    character.data["classes"][0]["level"] = level
    if level >= 3:
        character.data["subclasses"] = {"fighter": "champion"}
    if multiclass_ready:
        character.data["abilities"].update(strength=13, intelligence=13)
    character.data.update(participant_overrides or {})
    owner.create_campaign(
        CampaignCreate(
            campaign_id=campaign_id,
            display_name="Synthetic advancement",
            writer=writer,
            package_lock=candidate.package_lock,
            turn_id="turn",
            participants=(character,),
            initial_state=(
                StateValue(PACKAGE_ID, "combat", {"encounter_active": False}),
                StateValue(
                    PACKAGE_ID,
                    "state",
                    {
                        "advancement": {
                            "method": method,
                            "starting_level": level,
                            "maximum_level": 12,
                            "recipients": "whole_party",
                            "timing": "After the earned scene.",
                            "allocation": "The whole party advances together.",
                            "milestones": [
                                {
                                    "id": "rescue",
                                    "target_level": level + 1,
                                    "trigger": "Rescue resolved.",
                                    "alternative_outcomes": "Failure also counts.",
                                }
                            ],
                        }
                    },
                ),
                StateValue(
                    PACKAGE_ID,
                    "entities",
                    {
                        "hero": {
                            "hp": 5,
                            "max_hp": 10,
                            "is_alive": True,
                            "constitution_modifier": 0,
                            "fighter_level": level,
                            "resources": {"hit_die": 0, "second_wind": 1},
                            "resource_maxima": {"hit_die": level, "second_wind": 2},
                            "saving_throw_modifiers": {"constitution": 0},
                            "initiative_modifier": 0,
                            **(entity_overrides or {}),
                        }
                    },
                ),
            ),
        )
    )


def command(owner, name, parameters, action_id):
    return ActionCommand(
        campaign_id="advancement",
        action_id=action_id,
        idempotency_key=action_id,
        owner_id="test",
        writer=WriterRole.DM,
        expected_revision=owner.snapshot("advancement").revision,
        turn_id="turn",
        package_id=PACKAGE_ID,
        tool_name=name,
        parameters={"character": "hero", "character_state": "hero", **parameters},
    )


def award(owner, *, xp=0):
    return owner.execute_action(
        command(
            owner,
            "award_srd_advancement",
            {
                "award_id": "rescue",
                "xp": xp,
                "evidence": "The rescue was resolved in play.",
            },
            "award",
        )
    )


def build(owner):
    abilities = dict(owner.snapshot("advancement").participants[0].data["abilities"])
    return {
        "class_id": "fighter",
        "hp_method": "fixed",
        "choices_summary": "Gain the Fighter features for this level.",
        "build": [
            {
                "abilities": abilities,
                "skill_proficiencies": [],
                "tool_proficiencies": [],
                "weapon_proficiencies": [],
                "weapon_masteries": [],
                "saving_throw_proficiencies": [],
                "armor_class": 10,
                "speed": 30,
                "feature_hp_gain": 0,
                "mechanical_sources": "No additional HP, AC or speed changes.",
                "spellcasting": [],
                "resources": [],
                "prepared_spells": [],
                "features": [
                    {
                        "id": "fighter-level",
                        "source": "SRD Fighter",
                        "choice": "Selected this level's Fighter features.",
                    }
                ],
            }
        ],
    }


def resolve(owner, waiting, option="apply"):
    return owner.resolve_mechanics_choice(
        ResolveMechanicsChoiceCommand(
            campaign_id="advancement",
            action_id=waiting.action_id,
            resolution_id="confirm",
            owner_id="test",
            expected_revision=waiting.basis_revision,
            choice_id=waiting.choice.choice_id,
            option_id=option,
        )
    )


@pytest.mark.parametrize("retry", [False, True])
def test_gm_session_waits_for_level_choice_without_reading_an_uncommitted_result(tmp_path, retry):
    with AuthorityOwner.open(
        tmp_path / "choice-session.sqlite3", owner_id="test", disconnect_grace_seconds=5,
    ) as owner:
        create(owner)
        award(owner)
        session = owner.create_agent_session(
            campaign_id="advancement", role=WriterRole.DM,
            provider_id="fixture", model_id="fixture/model", session_id="choice-session",
        )
        loop = CampaignSessionLoop(owner)
        request = SessionRunRequest(session.session_id, "turn", "ask", "ask", "Level up.")
        submitted = []

        def provider(_input):
            action = replace(
                command(owner, "level_up_srd_character", build(owner), "level"),
                initiating_session_id=session.session_id,
            )
            submitted.append(action)
            yield ProviderEvent.action(action)
            pytest.fail("The provider must stop while the player has a pending choice")

        if retry:
            def interrupted(_input):
                raise RuntimeError("Synthetic provider failure before any action")

            with pytest.raises(RuntimeError, match="Synthetic provider failure"):
                loop.run(request, interrupted)
            loop.retry(
                session.session_id, retry_id="retry-choice",
                narrator=lambda *_: pytest.fail("Retry must use the original provider path"),
                provider_factory=lambda _: provider,
            )
        else:
            result = loop.run(request, provider)
            assert result.interruption is None
            assert isinstance(result.receipts[0], AwaitingChoiceReceipt)

        waiting = owner.execute_action(submitted[0])
        assert isinstance(waiting, AwaitingChoiceReceipt)
        assert owner.snapshot("advancement").participants[0].data["level"] == 1
        owner.resolve_mechanics_choice(ResolveMechanicsChoiceCommand(
            campaign_id="advancement", action_id=waiting.action_id, resolution_id="confirm",
            owner_id="test", expected_revision=waiting.basis_revision,
            choice_id=waiting.choice.choice_id, option_id="apply", session_id=session.session_id,
        ))
        assert owner.snapshot("advancement").participants[0].data["level"] == 2


def test_award_confirmation_commit_replay_and_reopen(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    with AuthorityOwner.open(path, owner_id="test") as owner:
        create(owner)
        assert award(owner).status is ActionStatus.COMMITTED
        request = command(owner, "level_up_srd_character", build(owner), "level")
        waiting = owner.execute_action(request)
        assert isinstance(waiting, AwaitingChoiceReceipt)
        assert owner.snapshot("advancement").participants[0].data["level"] == 1
        assert owner.execute_action(request) == waiting
        receipt = resolve(owner, waiting)
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.execute_action(request) == receipt
        snapshot = owner.snapshot("advancement")
        assert snapshot.participants[0].data["level"] == 2
        assert snapshot.participants[0].data["classes"] == [{"id": "fighter", "level": 2}]
        entity = snapshot.state[f"{PACKAGE_ID}.entities"]["hero"]
        assert entity["hp"] == 5
        assert entity["max_hp"] == 16
        assert entity["resources"] == {"hit_die": 1, "second_wind": 1}
        assert entity["resource_maxima"]["hit_die"] == 2
        assert any(
            item.safe_payload.get("kind") == "participant_updated" for item in snapshot.events
        )
        owner.export_campaign(
            operation_id="export",
            campaign_id="advancement",
            target=tmp_path / "levelled.sylvia-gm-campaign",
        )
    with AuthorityOwner.open(path, owner_id="test") as owner:
        assert owner.snapshot("advancement").participants[0].data["level"] == 2
    with AuthorityOwner.open(tmp_path / "recovered.sqlite3", owner_id="test") as owner:
        owner.recover_campaign(
            recovery_id="recover", source=tmp_path / "levelled.sylvia-gm-campaign"
        )
        assert owner.snapshot("advancement").participants[0].data["level"] == 2


def test_confirmed_campaign_reopens_after_a_committed_participant_update(tmp_path):
    path = tmp_path / "confirmed.sqlite3"
    with AuthorityOwner.open(path, owner_id="test") as owner:
        create(owner)
        source = owner.snapshot("advancement")
        confirmation = owner.confirm_new_campaign(
            operation_id="confirm-campaign", display_name="Confirmed advancement",
            package_lock=source.package_lock, turn_id="turn",
            package_candidate_id=owner.package_candidates()[0].candidate_id,
            builder_session_id="confirmed-builder", builder_provider_id="fixture",
            builder_model_id="fixture/model",
            participants=source.participants,
            initial_state=tuple(
                StateValue(PACKAGE_ID, key.removeprefix(PACKAGE_ID + "."), value)
                for key, value in source.state.items()
            ),
        )
        campaign_id = confirmation.campaign_id
        owner.begin_campaign_construction(
            operation_id="begin-confirmed", campaign_id=campaign_id, lane="quick_start",
            expected_revision=0, canonical_input={"premise": "Advancement controls."},
        )
        lock = owner.current_package_lock(campaign_id)
        primary = next(p for p in lock.package_lock.entries if p.package_id == PACKAGE_ID)
        owner.finalize_campaign_construction(
            campaign_id=campaign_id, dm_provider_id="fixture", dm_model_id="fixture/model",
            plan_artifact_text="Prepared advancement controls scenario.",
            model_guidance_override=True,
            structured_plan={
                "format_id": "sylvia-gm-campaign-plan",
                "format_version": CAMPAIGN_PLAN_FORMAT_VERSION,
                "bindings": {
                    "ruleset": {"id": primary.package_id, "version": primary.version,
                                "content_sha256": primary.sha256},
                    "campaign_setting": None, "package_closure_sha256": lock.closure_sha256,
                },
                "initial_state": {"mechanics": [{
                    "namespace": PACKAGE_ID, "key": "state",
                    "value": source.state[PACKAGE_ID + ".state"],
                }]},
            },
        )
        turn_id = "confirmed-dm-turn"
        owner.open_turn(campaign_id, turn_id)

        def execute(name, parameters, identity):
            return owner.execute_action(replace(
                command(owner, name, parameters, identity),
                campaign_id=campaign_id, writer=WriterRole.DM, turn_id=turn_id,
                expected_revision=owner.snapshot(campaign_id).revision,
            ))

        execute("award_srd_advancement", {
            "award_id": "rescue", "xp": 0, "evidence": "Prepared advancement scenario.",
        }, "confirmed-award")
        waiting = execute("level_up_srd_character", build(owner), "confirmed-level")
        owner.resolve_mechanics_choice(ResolveMechanicsChoiceCommand(
            campaign_id=campaign_id, action_id=waiting.action_id,
            resolution_id="confirmed-choice", owner_id="test",
            expected_revision=waiting.basis_revision,
            choice_id=waiting.choice.choice_id, option_id="apply",
        ))
        assert owner.snapshot(campaign_id).participants[0].data["level"] == 2
    with AuthorityOwner.open(path, owner_id="test") as owner:
        assert owner.snapshot(campaign_id).participants[0].data["level"] == 2
        assert owner.campaign_lifecycle(campaign_id).campaign_id == campaign_id
        advanced = owner.snapshot(campaign_id).participants[0].data
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE participants SET data_json = ? WHERE campaign_id = ?",
            (canonical_json_bytes({**advanced, "level": 3}).decode(), campaign_id),
        )
    with (
        pytest.raises(SchemaIdentityError, match="construction integrity"),
        AuthorityOwner.open(path, owner_id="test"),
    ):
        pass


def test_constitution_gain_applies_retroactively_without_healing(tmp_path):
    with AuthorityOwner.open(tmp_path / "constitution.sqlite3", owner_id="test") as owner:
        create(owner, level=3)
        award(owner)
        proposal = build(owner)
        proposal["build"][0]["abilities"]["constitution"] = 12
        proposal["build"][0]["resources"] = [
            {
                "id": "action_surge",
                "maximum": 1,
                "source": "SRD Fighter",
            }
        ]
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        resolve(owner, waiting)
        entity = owner.snapshot("advancement").state[f"{PACKAGE_ID}.entities"]["hero"]
        assert entity["hp"] == 5
        assert entity["max_hp"] == 20  # d10 fixed 6 + Con 1, plus Con increase for 3 earlier levels
        assert entity["constitution_modifier"] == 1
        assert entity["saving_throw_modifiers"]["constitution"] == 1
        assert (
            entity["resources"]["second_wind"] == 2
        )  # One spent use remains spent as the maximum grows.
        assert entity["resource_maxima"]["second_wind"] == 3
        assert entity["resources"]["action_surge"] == 1


def test_adapted_character_can_advance_and_reopen_with_its_provenance(tmp_path):
    path = tmp_path / "adapted.sqlite3"
    with AuthorityOwner.open(path, owner_id="test") as owner:
        create(owner, writer=WriterRole.BUILDER, campaign_id="source")
        source = owner.snapshot("source")
        character = source.participants[0]
        adapted = owner.adapt_participant_artifact(
            source_campaign_id="source",
            participant_id="hero",
            destination_package_id=character.package_id,
            destination_package_version=character.package_version,
            destination_package_sha256=character.package_sha256,
            destination_kind=character.kind,
            canonical_data=character.data,
            operation_id="adapt",
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="advancement",
                display_name="Adapted advancement",
                writer=WriterRole.DM,
                package_lock=source.package_lock,
                turn_id="turn",
                participants=(adapted,),
                initial_state=tuple(
                    StateValue(PACKAGE_ID, key.removeprefix(PACKAGE_ID + "."), value)
                    for key, value in source.state.items()
                ),
            )
        )
        award(owner)
        waiting = owner.execute_action(
            command(owner, "level_up_srd_character", build(owner), "level")
        )
        assert resolve(owner, waiting).status is ActionStatus.COMMITTED
    with AuthorityOwner.open(path, owner_id="test") as owner:
        participant = owner.snapshot("advancement").participants[0]
        assert participant.data["level"] == 2
        assert participant.adaptation_id == "adapt"
        assert owner.snapshot("source").participants[0].data["level"] == 1


def test_level_five_bonus_is_used_by_the_next_real_check(tmp_path):
    with AuthorityOwner.open(tmp_path / "proficiency.sqlite3", owner_id="test") as owner:
        create(owner, level=4)
        award(owner)
        proposal = build(owner)
        proposal["build"][0]["skill_proficiencies"] = ["athletics"]
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        resolve(owner, waiting)
        receipt = owner.execute_action(
            ActionCommand(
                campaign_id="advancement",
                action_id="check",
                idempotency_key="check",
                owner_id="test",
                writer=WriterRole.DM,
                expected_revision=2,
                turn_id="turn",
                package_id=PACKAGE_ID,
                tool_name="d20_test",
                parameters={
                    "actor": "hero",
                    "actor_state": "hero",
                    "ability": "strength",
                    "skill": "athletics",
                    "kind": "ability_check",
                    "proficiency_factor": "normal",
                    "circumstantial_modifier": 0,
                    "target_number": 10,
                },
            )
        )
        assert receipt.status is ActionStatus.COMMITTED
        assert receipt.result["context"]["_proficiency_bonus"] == 3


@pytest.mark.parametrize(
    "change", ["unearned_ability", "missing_features", "multiclass_prerequisite"]
)
def test_invalid_builds_leave_award_available(tmp_path, change):
    with AuthorityOwner.open(tmp_path / "invalid.sqlite3", owner_id="test") as owner:
        create(owner)
        award(owner)
        before = owner.snapshot("advancement")
        proposal = build(owner)
        if change == "unearned_ability":
            proposal["build"][0]["abilities"]["strength"] = 12
        elif change == "missing_features":
            proposal["build"][0]["features"] = []
        else:
            proposal["class_id"] = "wizard"
        with pytest.raises(CommandRejected):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "invalid"))
        after = owner.snapshot("advancement")
        assert after.state == before.state
        assert after.participants == before.participants


def test_xp_can_accumulate_without_announcing_an_unearned_level(tmp_path):
    with AuthorityOwner.open(tmp_path / "xp.sqlite3", owner_id="test") as owner:
        create(owner, method="xp")
        assert award(owner, xp=100).status is ActionStatus.COMMITTED
        entity = owner.snapshot("advancement").state[f"{PACKAGE_ID}.entities"]["hero"]
        assert entity["experience_points"] == 100
        assert entity["advancement_target"] == 1
        with pytest.raises(CommandRejected):
            owner.execute_action(
                command(owner, "level_up_srd_character", build(owner), "not-ready")
            )


def test_multiclass_hit_die_is_spent_with_its_own_size(tmp_path):
    with AuthorityOwner.open(tmp_path / "multiclass.sqlite3", owner_id="test") as owner:
        create(owner, multiclass_ready=True)
        award(owner)
        proposal = build(owner)
        proposal["class_id"] = "wizard"
        proposal["build"][0]["resources"] = [
            {
                "id": "spell_slot_1",
                "maximum": 2,
                "source": "SRD Wizard level 1",
            }
        ]
        proposal["build"][0]["spellcasting"] = [{
            "source": "wizard", "ability": "intelligence",
            "spells": ["fire_bolt", "light", "mage_hand", "magic_missile", "shield", "sleep",
                       "burning_hands"],
        }]
        proposal["build"][0]["wizard_spellbook"] = [
            "magic_missile", "shield", "sleep", "burning_hands", "detect_magic", "grease",
        ]
        proposal["build"][0]["prepared_spells"] = proposal["build"][0]["spellcasting"][0]["spells"]
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        resolve(owner, waiting)
        snapshot = owner.snapshot("advancement")
        pools = snapshot.state[f"{PACKAGE_ID}.entities"]["hero"]["hit_dice_pools"]
        assert pools == [
            {"class_id": "fighter", "size": 10, "remaining": 0, "maximum": 1},
            {"class_id": "wizard", "size": 6, "remaining": 1, "maximum": 1},
        ]
        for tool, parameters in [
            ("begin_srd_rest", {"target_state": "hero", "kind": "short", "activity": "light"}),
            ("advance_srd_lasting_time", {"elapsed_rounds": 600}),
            ("complete_srd_rest", {"target_state": "hero"}),
        ]:
            revision = owner.snapshot("advancement").revision
            rest = owner.execute_action(ActionCommand(
                campaign_id="advancement", action_id=f"rest-{revision}",
                idempotency_key=f"rest-{revision}", owner_id="test", writer=WriterRole.DM,
                expected_revision=revision, turn_id="turn", package_id=PACKAGE_ID,
                tool_name=tool, parameters=parameters,
            ))
            assert rest.status is ActionStatus.COMMITTED, rest.result
        receipt = owner.execute_action(
            ActionCommand(
                campaign_id="advancement",
                action_id="rest",
                idempotency_key="rest",
                owner_id="test",
                writer=WriterRole.DM,
                expected_revision=owner.snapshot("advancement").revision,
                turn_id="turn",
                package_id=PACKAGE_ID,
                tool_name="take_srd_short_rest_hit_die",
                parameters={"target_state": "hero", "hit_die_class": "wizard"},
            )
        )
        assert receipt.status is ActionStatus.COMMITTED
        assert receipt.result["context"]["_short_rest_hit_die_roll"]["sides"] == 6
        entity = owner.snapshot("advancement").state[f"{PACKAGE_ID}.entities"]["hero"]
        assert entity["resources"]["hit_die"] == 0
        assert all(pool["remaining"] == 0 for pool in entity["hit_dice_pools"])
        for name, parameters, expected in [
            ("restore_srd_resource", {"amount": 2}, [1, 1]),
            ("spend_srd_resource", {"amount": 1, "hit_die_class": "fighter"}, [0, 1]),
            ("restore_srd_resource", {"amount": 1, "hit_die_class": "fighter"}, [1, 1]),
        ]:
            revision = owner.snapshot("advancement").revision
            result = owner.execute_action(
                ActionCommand(
                    campaign_id="advancement",
                    action_id=f"resource-{revision}",
                    idempotency_key=f"resource-{revision}",
                    owner_id="test",
                    writer=WriterRole.DM,
                    expected_revision=revision,
                    turn_id="turn",
                    package_id=PACKAGE_ID,
                    tool_name=name,
                    parameters={"target": "hero", "resource": "hit_die", **parameters},
                )
            )
            assert result.status is ActionStatus.COMMITTED
            entity = owner.snapshot("advancement").state[f"{PACKAGE_ID}.entities"]["hero"]
            assert [pool["remaining"] for pool in entity["hit_dice_pools"]] == expected
            assert entity["resources"]["hit_die"] == sum(expected)


@pytest.mark.parametrize("fault", [None, "empty", "milestones", "xp_sources", "range"])
def test_policy_requires_a_complete_declared_method(fault):
    with admit_package_archive(io.BytesIO(bundled_srd_archive())) as archive:
        manifest = next(item for item in archive.manifests if item.package_id == PACKAGE_ID)
        policy = {
            "method": "fixed",
            "starting_level": 1,
            "maximum_level": 1,
            "recipients": "whole_party",
            "timing": "No level-ups.",
            "allocation": "The party remains at its declared level.",
        }
        if fault == "empty":
            policy["timing"] = ""
        elif fault == "milestones":
            policy["method"] = "milestone"
        elif fault == "xp_sources":
            policy["method"] = "xp"
        elif fault == "range":
            policy["starting_level"] = 5
        payload = {
            "initial_state": {
                "mechanics": [
                    {
                        "namespace": PACKAGE_ID,
                        "key": "state",
                        "value": {"advancement": policy},
                    }
                ]
            }
        }
        if fault is None:
            validate_plan_state(payload, manifest)
        else:
            with pytest.raises(ContentShapeError):
                validate_plan_state(payload, manifest)


def test_plan_admission_enforces_the_retained_required_policy(tmp_path):
    with AuthorityOwner.open(tmp_path / "plan.sqlite3", owner_id="test") as owner:
        create(owner, writer=WriterRole.BUILDER)
        owner.create_agent_session(
            campaign_id="advancement",
            role=WriterRole.BUILDER,
            provider_id="synthetic",
            model_id="synthetic",
            session_id="builder",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.record_builder_evidence(
            session_id="builder",
            evidence_id="evidence",
            source_kind="user_provided",
            content_sha256=hashlib.sha256(b"synthetic plan").hexdigest(),
            source_locator=None,
            safe_excerpt="Synthetic Plan policy.",
            claims=("policy",),
        )

        def admit(content, identity):
            return owner.admit_campaign_plan(
                plan_id=identity,
                campaign_id="advancement",
                builder_session_id="builder",
                expected_revision=0,
                expected_package_lock_id=owner.current_package_lock("advancement").lock_id,
                content=content,
                content_sha256=hashlib.sha256(canonical_json_bytes(content)).hexdigest(),
                provenance_evidence_ids=("evidence",),
                validation_outcome="valid",
                acceptance_status="accepted",
            )

        with pytest.raises(CommandRejected) as failure:
            admit({"campaign_plan": {"initial_state": {"mechanics": []}}}, "missing")
        assert failure.value.code == "plan_required_state_invalid"
        state = owner.snapshot("advancement").state[f"{PACKAGE_ID}.state"]
        accepted = admit(
            {
                "campaign_plan": {
                    "initial_state": {
                        "mechanics": [
                            {
                                "namespace": PACKAGE_ID,
                                "key": "state",
                                "value": state,
                            }
                        ]
                    }
                }
            },
            "complete",
        )
        assert accepted.plan_id == "complete"


def test_live_profile_replacements_reject_foreign_owners_and_invalid_schema(tmp_path):
    with AuthorityOwner.open(tmp_path / "ownership.sqlite3", owner_id="test") as owner:
        create(owner)
        character = owner.snapshot("advancement").participants[0]
        for package_id, data in [
            ("foreign.rules", character.data),
            (PACKAGE_ID, {**character.data, "level": "two"}),
        ]:
            assert not owner._store.mechanic_mutations_are_owned(
                campaign_id="advancement",
                package_id=package_id,
                package_version=character.package_version,
                package_sha256=character.package_sha256,
                outcome=MechanicOutcome(
                    result={}, participant_mutations=(ParticipantMutation("hero", data),)
                ),
            )
        assert owner.snapshot("advancement").participants[0].data["level"] == 1


def test_cancel_preserves_award_and_character(tmp_path):
    with AuthorityOwner.open(tmp_path / "cancel.sqlite3", owner_id="test") as owner:
        create(owner)
        award(owner)
        before = owner.snapshot("advancement")
        waiting = owner.execute_action(
            command(owner, "level_up_srd_character", build(owner), "level")
        )
        resolve(owner, waiting, "cancel")
        after = owner.snapshot("advancement")
        assert after.participants == before.participants
        assert after.state == before.state


def test_duplicate_award_and_unearned_level_do_not_change_truth(tmp_path):
    with AuthorityOwner.open(tmp_path / "reject.sqlite3", owner_id="test") as owner:
        create(owner)
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected):
            owner.execute_action(command(owner, "level_up_srd_character", build(owner), "too-soon"))
        assert owner.snapshot("advancement").state == before.state
        award(owner)
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected):
            owner.execute_action(
                command(
                    owner,
                    "award_srd_advancement",
                    {
                        "award_id": "rescue",
                        "xp": 0,
                        "evidence": "Repeated request.",
                    },
                    "duplicate",
                )
            )
        assert owner.snapshot("advancement").state == before.state
