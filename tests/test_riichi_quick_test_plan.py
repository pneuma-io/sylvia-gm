"""The bundled one-match Plan reaches ordinary Riichi play with its included player."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_campaign_plans import bundled_plan, bundled_plan_files
from sylvia_gm.application.bundled_packages import bundled_riichi_archive
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    plan_with_interview_intent,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.interview_contribution import (
    parse_interview_contribution_bytes,
    validate_interview_contribution_answers,
)
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


@pytest.mark.parametrize("match_format", ["east_only", "east_south"])
def test_included_player_and_format_reach_a_real_engine_deal(tmp_path: Path, match_format: str):
    selected = bundled_plan("riichi-quick-test")
    assert selected is not None
    files = bundled_plan_files(Path(__file__).resolve().parents[1] / "campaign-plans")
    assert any(path.as_posix().endswith("riichi-quick-test/setting-files/characters/you.json")
               for path in files)
    with admit_package_archive(io.BytesIO(bundled_riichi_archive())) as original:
        expected_rules = next(item for item in original.manifests
                              if item.package_id == "pneuma.riichi-mahjong")
        with admit_package_archive(io.BytesIO(selected.package_snapshot)) as snapshot:
            assert next(item for item in snapshot.manifests
                        if item.package_id == "pneuma.riichi-mahjong") == expected_rules
            original_world = next(item for item in original.files if item.file.path == "world.md")
            saved_world = next(item for item in snapshot.files if item.file.path == "world.md")
            assert b"".join(original.iter_file_chunks(original_world)) == b"".join(
                snapshot.iter_file_chunks(saved_world)
            )
    contribution = parse_interview_contribution_bytes(
        canonical_json_bytes(selected.interview_contribution), expected_owner_kind="campaign_plan",
    )
    answer = validate_interview_contribution_answers(contribution, {
        "contribution_id": contribution.contribution_id, "answers": {"match_format": match_format},
    })
    with AuthorityOwner.open(tmp_path / "deal.db", "quick-test") as owner:
        selection = resolve_campaign_library_selection(
            owner, tmp_path, candidate_id="pneuma.riichi-mahjong",
            setting_id="pneuma.riichi-club", plan_id="riichi-quick-test",
        )
        options = selection.party_options()
        assert len(options["player_archetypes"]) == 1
        assert options["player_archetypes"][0]["name"] == "You"
        assert options["player_archetypes"][0]["campaign_ready"] is True
        assert options["companions"] == []
        participants, initial_state, _ = materialize_library_party(
            owner, selection, operation_id="quick-test-start",
            player_character_id=options["player_archetypes"][0]["id"], companion_ids=(),
        )
        assert len(participants) == 1
        assert participants[0].kind == "player"
        assert participants[0].data == {"name": "You", "control": "player"}
        accepted = plan_with_interview_intent(
            selection, {"contributions": {"plan_contribution": answer}},
        )
        assert f"match_format={match_format}" in accepted.payload["gm_guidance"]["instructions"]
        assert selected.plan.payload["initial_state"]["mechanics"] == []
        owner.create_campaign(CampaignCreate(
            campaign_id="quick-match", display_name="Riichi Quick Test", writer=WriterRole.DM,
            package_lock=selection.candidate.package_lock, turn_id="first-turn",
            participants=participants, initial_state=initial_state,
        ))
        before = owner.snapshot("quick-match")
        result = owner.execute_action(ActionCommand(
            campaign_id="quick-match", action_id="normal-start", idempotency_key="normal-start",
            turn_id="first-turn", owner_id="quick-test", writer=WriterRole.DM,
            expected_revision=before.revision, package_id="pneuma.riichi-mahjong",
            tool_name="start_game", parameters={"player": participants[0].participant_id,
                                               "match_format": match_format},
        ))
        assert result.status.value == "committed"
        after = owner.snapshot("quick-match")
        state = after.state["pneuma.riichi-mahjong.state"]
        entities = after.state["pneuma.riichi-mahjong.entities"]
        assert state["match_format"] == match_format
        assert state["phase"] == "discard" and state["actor"] == state["seats"][0]
        assert [len(entities[seat]["game"]["hand"]) for seat in state["seats"]] == [14, 13, 13, 13]
        assert [entities[seat]["control"] for seat in state["seats"]] == [
            "player", "gm", "gm", "gm",
        ]
        assert [entities[seat]["score"] for seat in state["seats"]] == [25000] * 4
        replay = owner.execute_action(ActionCommand(
            campaign_id="quick-match", action_id="normal-start", idempotency_key="normal-start",
            turn_id="first-turn", owner_id="quick-test", writer=WriterRole.DM,
            expected_revision=before.revision, package_id="pneuma.riichi-mahjong",
            tool_name="start_game", parameters={"player": participants[0].participant_id,
                                               "match_format": match_format},
        ))
        assert replay == result and owner.snapshot("quick-match") == after


def test_default_discovery_reopens_without_replacing_an_authored_plan(tmp_path: Path):
    original = bundled_plan("riichi-quick-test")
    assert original is not None
    # Ordinary authoring retains the selected sources before saving an edited Plan.
    with AuthorityOwner.open(tmp_path / "profile.db", "prepare-authored-plan") as owner:
        owner.install_package_archive(
            io.BytesIO(original.package_snapshot), install_id="authored-plan-sources",
        )
    payload = json.loads(canonical_json_bytes(original.plan.payload))
    payload.update(id="my-riichi-evening", title="My edited Riichi evening")
    authored = save_reusable_campaign_plan(
        tmp_path, operation_id="save-my-plan", source_payload=payload,
        source_markdown="# My evening\n\nKeep my authored table introduction.\n",
        package_snapshot=original.package_snapshot,
        validate_plan=lambda payload, text: validate_plan_snapshot(
            original.package_snapshot, payload, text,
        ),
    )
    snapshots = []
    for _ in range(2):
        harness = _OwnerHarness(tmp_path / "profile.db", None)
        try:
            catalog = harness.request("catalog", "campaign_creation_catalog")
            matches = [item for item in catalog if item["kind"] == "campaign_plan"
                       and item.get("ruleset", {}).get("id") == "pneuma.riichi-mahjong"]
            assert {item["id"] for item in matches} == {"riichi-quick-test", authored.artifact_id}
            assert next(item for item in matches if item["id"] == authored.artifact_id)[
                "content_sha256"
            ] == authored.content_sha256
            options = harness.request("party", "campaign_party_options", {
                "ruleset_selection": "pneuma.riichi-mahjong", "setting_id": "pneuma.riichi-club",
                "plan_id": "riichi-quick-test",
            })
            assert len(options["player_archetypes"]) == 1
            assert options["player_archetypes"][0]["name"] == "You"
            assert options["player_archetypes"][0]["campaign_ready"] is True
            assert options["companions"] == []
            snapshots.append(harness.request("candidates", "package_candidates"))
        finally:
            harness.close()
    assert snapshots[0] == snapshots[1]
