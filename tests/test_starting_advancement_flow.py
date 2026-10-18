"""Normal choice receipts advance campaign-local starting queues once, across restarts."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate, ResolveMechanicsChoiceCommand
from sylvia_gm.application.bundled_packages import bundled_rift_authority
from sylvia_gm.application.campaign_library_selection import materialize_library_party, resolve_campaign_library_selection
from sylvia_gm.application.reusable_participants import save_reusable_participant
from sylvia_gm.domain.authority import ActionStatus, AwaitingChoiceReceipt, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_reusable_participants import _participant, _participant_authority, _starting_state

PACKAGE = "sylvia.srd-lite.bg3"


def _seed(owner, root, target=2, companions=1):
    package, declaration = _participant_authority()
    setting = bundled_rift_authority().setting
    saved = tuple(save_reusable_participant(
        root, operation_id=f"card-{i}", role="player_character" if i == 0 else "companion",
        summary="A synthetic base-level fighter.", authored_profile="A harbor fighter preparing for an exhibition.",
        participant=_participant(f"Base fighter {i}"), entity_state=_starting_state(max_hp=11),
        package=package, declaration=declaration, campaign_setting=setting,
    ) for i in range(companions + 1))
    selection = resolve_campaign_library_selection(owner, root, candidate_id=package.package_id,
                setting_id=setting.package_id, plan_id="the-sparring-debt")
    payload = json.loads(canonical_json_bytes(selection.plan.plan.payload))
    policy = next(item["value"]["advancement"] for item in payload["initial_state"]["mechanics"]
                  if item["namespace"] == PACKAGE and item["key"] == "state")
    policy.update(starting_level=target, maximum_level=target,
                  timing=f"This synthetic campaign starts at level {target} and stays there.",
                  allocation="Whole-party starting preparation only; no earned advancement.")
    checked = selection.sources.validate(payload, selection.plan.plan.readable_markdown)
    selection = replace(selection, plan=replace(selection.plan, plan=checked))
    party, state, _ = materialize_library_party(owner, selection, operation_id="initial-start",
                player_character_id=saved[0].artifact_id, companion_ids=tuple(x.artifact_id for x in saved[1:]))
    owner.create_campaign(CampaignCreate(campaign_id="initial-start", display_name="Starting levels",
                writer=WriterRole.DM, package_lock=selection.candidate.package_lock, turn_id="turn",
                participants=party, initial_state=state))
    return saved


def _command(owner, actor, identity, parameters, tool="level_up_srd_character"):
    return ActionCommand(campaign_id="initial-start", action_id=identity, idempotency_key=identity,
                owner_id="test", writer=WriterRole.DM, expected_revision=owner.snapshot("initial-start").revision,
                turn_id="turn", package_id=PACKAGE, tool_name=tool,
                parameters={"character": actor, "character_state": actor, **parameters})


def _proposal(owner, actor):
    snapshot = owner.snapshot("initial-start")
    profile = next(p.data for p in snapshot.participants if p.participant_id == actor)
    entity = snapshot.state[PACKAGE + ".entities"][actor]
    target = profile["level"] + 1
    abilities = dict(profile["abilities"])
    if target == 4:
        abilities["strength"] += 2  # Explicit synthetic player choice at this level.
    build = {
        "abilities": abilities, "armor_class": entity["armor_class"], "speed": profile["speed"],
        "feature_hp_gain": 0, "mechanical_sources": "SRD Fighter and the retained worn equipment; fixed d10 HP, Constitution unchanged.",
        "spellcasting": list(profile.get("spellcasting_grants", ())),
        "prepared_spells": list(entity.get("prepared_spells", ())),
        "resources": [{"id": "action_surge", "maximum": 1, "source": "SRD Fighter level 2"}],
        "features": [{"id": f"fighter-{target}", "source": "SRD Fighter",
                      "choice": "Fighter level, fixed HP; Champion at 3; Strength +2 at 4."}],
    }
    for field in ("skill_proficiencies", "tool_proficiencies", "weapon_proficiencies",
                  "weapon_masteries", "saving_throw_proficiencies"):
        build[field] = list(profile.get(field, ()))
    if "fighting_styles" in profile:
        build["fighting_styles"] = list(profile["fighting_styles"])
    if target >= 3:
        build["subclasses"] = {"fighter": "champion"}
    if target == 3:
        build["features"].append({"id": "subclass", "source": "SRD Fighter: Champion",
                                  "choice": "Champion selected explicitly for this synthetic player."})
    if target == 4:
        build["features"].append({"id": "ability_score_improvement", "source": "SRD Fighter level 4",
                                  "choice": "Strength +2, explicitly selected for this synthetic player."})
    return {"class_id": "fighter", "hp_method": "fixed",
            "choices_summary": f"Advance one queued step to {target}; retain prior choices, Champion at 3 and Strength +2 at 4.",
            "build": [build]}


def _resolve(owner, waiting, identity, option="apply"):
    return ResolveMechanicsChoiceCommand(campaign_id="initial-start", action_id=waiting.action_id,
                resolution_id=identity, owner_id="test", expected_revision=waiting.basis_revision,
                choice_id=waiting.choice.choice_id, option_id=option)


@pytest.mark.parametrize("target", [1, 2, 4, 20])
def test_initial_queue_is_durable_ordered_and_changes_no_card_or_starting_resources(tmp_path, target):
    database = tmp_path / "campaign.db"
    with AuthorityOwner.open(database, "test") as owner:
        saved = _seed(owner, tmp_path, target=target, companions=1)
        snapshot = owner.snapshot("initial-start")
        before = canonical_json_bytes({"state": snapshot.state, "participants": [p.data for p in snapshot.participants]})
        assert all(p.data["level"] == 1 for p in snapshot.participants)
        for entity in snapshot.state[PACKAGE + ".entities"].values():
            if "initial_advancement_queue" not in entity:
                continue
            assert entity["initial_advancement_queue"] == [
                {"from_level": level - 1, "target_level": level, "intent": "level_up_srd_character"}
                for level in range(2, target + 1)]
            assert entity["hp"] == entity["max_hp"] == 11
            assert entity.get("advancement_awards") is None
            assert entity.get("experience_points") is None
        boundary = owner.model_decision_boundary("initial-start")
        assert (boundary is not None and boundary.preparation is not None) is (target > 1)
    card_bytes = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                  for p in (tmp_path / "participants").rglob("*") if p.is_file()}
    with AuthorityOwner.open(database, "test") as owner:
        restored = owner.snapshot("initial-start")
        assert canonical_json_bytes({"state": restored.state, "participants": [p.data for p in restored.participants]}) == before
    assert all(card.participant["level"] == 1 for card in saved)
    assert card_bytes == {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                         for p in (tmp_path / "participants").rglob("*") if p.is_file()}


def test_normal_choices_cancel_reopen_and_replay_do_not_duplicate_initial_levels_or_awards(tmp_path):
    database = tmp_path / "campaign.db"
    owner = AuthorityOwner.open(database, "test")
    try:
        _seed(owner, tmp_path, target=4, companions=1)
        original_cards = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                          for p in (tmp_path / "participants").rglob("*") if p.is_file()}
        boundary = owner.model_decision_boundary("initial-start")
        first = boundary.actor_id
        before = owner.snapshot("initial-start")
        with pytest.raises(CommandRejected) as blocked:
            owner.execute_action(_command(owner, first, "cannot-play", {}, tool="end_combat_turn"))
        assert blocked.value.code == "campaign_preparation_pending"
        other = next(p.participant_id for p in before.participants if p.participant_id != first)
        with pytest.raises(CommandRejected) as blocked:
            owner.execute_action(_command(owner, other, "wrong-order", _proposal(owner, other)))
        assert blocked.value.code == "campaign_preparation_pending"
        assert owner.snapshot("initial-start").revision == before.revision
        cancelled_request = _command(owner, first, "cancelled-start", _proposal(owner, first))
        cancelled = owner.execute_action(cancelled_request)
        assert isinstance(cancelled, AwaitingChoiceReceipt)
        receipt = owner.resolve_mechanics_choice(_resolve(owner, cancelled, "cancel-choice", "cancel"))
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("initial-start").participants == before.participants
        assert owner.snapshot("initial-start").state == before.state
        committed = 0
        while (boundary := owner.model_decision_boundary("initial-start")) is not None and boundary.preparation is not None:
            actor = boundary.actor_id
            before = owner.snapshot("initial-start")
            proposal = _proposal(owner, actor)
            target = next(p.data["level"] for p in before.participants if p.participant_id == actor) + 1
            if target == 3:
                missing = deepcopy(proposal)
                missing["build"][0].pop("subclasses")
                with pytest.raises(CommandRejected):
                    owner.execute_action(_command(owner, actor, f"missing-subclass-{committed}", missing))
                assert owner.snapshot("initial-start").state == before.state
                assert owner.snapshot("initial-start").participants == before.participants
            request = _command(owner, actor, f"starting-event-{committed}", proposal)
            waiting = owner.execute_action(request)
            assert isinstance(waiting, AwaitingChoiceReceipt)
            assert owner.execute_action(request) == waiting
            assert owner.snapshot("initial-start").participants == before.participants
            if committed == 0:
                owner.close()
                owner = AuthorityOwner.open(database, "test")
                assert owner.execute_action(request) == waiting
                assert owner.snapshot("initial-start").state == before.state
            resolution = _resolve(owner, waiting, f"apply-start-{committed}")
            receipt = owner.resolve_mechanics_choice(resolution)
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.resolve_mechanics_choice(resolution) == receipt
            assert owner.execute_action(request) == receipt
            after = owner.snapshot("initial-start")
            profile = next(p.data for p in after.participants if p.participant_id == actor)
            entity = after.state[PACKAGE + ".entities"][actor]
            assert profile["level"] == target
            assert all(event["target_level"] > target for event in entity["initial_advancement_queue"])
            assert entity.get("advancement_awards") is None and entity.get("experience_points") is None
            assert entity["hp"] == 11  # Existing normal advancement does not heal.
            committed += 1
        assert committed == 6
        finished = owner.snapshot("initial-start")
        assert all(p.data["level"] == 4 for p in finished.participants)
        assert all(len(p.data["advancements"]) == 3 for p in finished.participants)
        assert all(entity["initial_advancement_queue"] == []
                   for entity in finished.state[PACKAGE + ".entities"].values()
                   if "initial_advancement_queue" in entity)
        finished_bytes = canonical_json_bytes({"state": finished.state, "participants": [p.data for p in finished.participants]})
        owner.close()
        owner = AuthorityOwner.open(database, "test")
        reopened = owner.snapshot("initial-start")
        assert canonical_json_bytes({"state": reopened.state, "participants": [p.data for p in reopened.participants]}) == finished_bytes
        assert owner.model_decision_boundary("initial-start") is None
        assert original_cards == {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                                  for p in (tmp_path / "participants").rglob("*") if p.is_file()}
    finally:
        owner.close()


def test_actual_starting_choice_has_one_durable_automatic_response_origin(tmp_path):
    from sylvia_gm.application.player_actions import valid_player_action_consequence, PLAYER_ACTION_ORIGIN
    from sylvia_gm.domain.errors import IdempotencyConflict
    database = tmp_path / "choice.db"
    with AuthorityOwner.open(database, "test") as owner:
        _seed(owner, tmp_path, target=2, companions=0)
        owner.create_agent_session(campaign_id="initial-start", role=WriterRole.DM,
            provider_id="fixture", model_id="fixture/model", session_id="starting-dm")
        actor = owner.campaign_preparation_boundary("initial-start").actor_id
        request = replace(_command(owner, actor, "choice-with-session", _proposal(owner, actor)),
                          initiating_session_id="starting-dm")
        waiting = owner.execute_action(request)
        assert isinstance(waiting, AwaitingChoiceReceipt)
        resolution = replace(_resolve(owner, waiting, "actual-choice"), session_id="starting-dm")
        with pytest.raises(IdempotencyConflict):
            owner.resolve_mechanics_choice(replace(resolution, session_id="wrong-session"))
        assert owner.snapshot("initial-start").participants[0].data["level"] == 1
        receipt = owner.resolve_mechanics_choice(resolution)
        assert receipt.status is ActionStatus.COMMITTED
        record = owner.player_action_response_basis("starting-dm", request.action_id, automatic=True)
        origin = record["origin"]
        assert origin["control"] == "mechanics_choice"
        assert origin["source_action_id"] == request.action_id
        assert origin["choice_id"] == waiting.choice.choice_id
        assert origin["option_id"] == "apply" and origin["resolution_id"] == "actual-choice"
        assert origin["choice_sequence"] == 0
        assert origin["session_id"] == "starting-dm" and "text" not in origin
        assert valid_player_action_consequence({"kind": PLAYER_ACTION_ORIGIN, "origin": origin},
                                               expected_revision=waiting.basis_revision)
        assert not valid_player_action_consequence({"kind": PLAYER_ACTION_ORIGIN,
            "origin": {**origin, "choice_sequence": -1}}, expected_revision=waiting.basis_revision)
        revision = owner.snapshot("initial-start").revision
        assert owner.resolve_mechanics_choice(resolution) == receipt
        assert owner.snapshot("initial-start").revision == revision
        assert len(owner.player_action_context("initial-start")) == 1
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.player_action_response_basis("starting-dm", request.action_id, automatic=True) == record
        assert owner.snapshot("initial-start").participants[0].data["level"] == 2
        assert owner.campaign_preparation_boundary("initial-start") is None
