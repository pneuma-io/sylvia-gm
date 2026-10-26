"""Saved non-D&D Plans and characters must reach the actual campaign handoff."""

import json
from dataclasses import replace

import pytest

from sylvia_gm.agent._builder_session import _campaign_plan_example
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.reusable_participants import save_reusable_participant
from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan
from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import install_character_packages
from tests.test_tactical_grid_ui import _scene


def seed_campaign_library(owner, root, *, required_party=False):
    setting_characters = (
        tuple(
            {
                "id": identity,
                "summary": f"{name}, a relay operator.",
                "authored_profile": f"# {name}\n\nTrained at the north relay.",
                "participant": {"name": name, "callsign": name},
                "entity_state": {"stress": stress},
            }
            for identity, name, stress in (("captain", "Captain", 11), ("guide", "Guide", 12))
        )
        if required_party
        else ()
    )
    candidate, setting_id = install_character_packages(owner, setting_characters=setting_characters)
    sources = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting_id)
    payload = json.loads(_campaign_plan_example(sources))
    payload.update(id="relay-intrusion", title="Relay Intrusion", premise="Find the lost operator.")
    payload["starting_party"]["custom_companions"] = {"minimum": 0, "maximum": 1}
    if required_party:
        payload["starting_party"]["player_character"] = {
            "mode": "fixed",
            "character": {"id": "captain", "path": "characters/captain.json"},
        }
        payload["starting_party"]["required_companions"] = [
            {"id": "guide", "path": "characters/guide.json"},
        ]
    payload["initial_state"]["facts"] = ["The relay is abandoned."]
    payload["initial_state"]["mechanics"] = [
        {
            "namespace": sources.ruleset.package_id,
            "key": "state",
            "value": {
                "entities": {"relay-keeper": {"stress": 4}},
                "resources": {"charge": 8},
                "spatial_scene": _scene(),
                "eligible_actors": [],
            },
        }
    ]
    payload["initial_state"]["party_placements"] = [
        {
            "entity_state": {"id": {"$participant": "id"}, "position_cell": [column, 1]},
            "append": [
                {
                    "namespace": sources.ruleset.package_id,
                    "key": "state.spatial_scene.entities",
                    "value": {
                        **_scene()["entities"][0],
                        "id": {"$participant": "id"},
                        "position": [column + 0.5, 1.5, 0],
                        "metadata": {
                            "player": {
                                "label": {"$participant": "name"},
                                "kind": "unit",
                                "side": "allies",
                            }
                        },
                    },
                },
                {
                    "namespace": sources.ruleset.package_id,
                    "key": "state.eligible_actors",
                    "value": {"$participant": "id"},
                },
            ],
        }
        for column in (1, 2, 3)
    ]
    plan = save_reusable_campaign_plan(
        root,
        operation_id="relay-plan",
        source_payload=payload,
        source_markdown="# Relay Intrusion\n\nFind the lost operator.",
        validate_plan=sources.validate,
    )
    primary = next(
        item for item in candidate.package_lock.entries if item.role is PackageRole.PRIMARY
    )
    declaration = owner.package_participant_declarations(candidate.candidate_id)[0]
    characters = tuple(
        save_reusable_participant(
            root,
            operation_id=f"save-{role}",
            role=role,
            summary=f"{name}, a relay operator.",
            authored_profile=f"# {name}\n\nTrained at the north relay.",
            participant={"name": name, "callsign": name},
            entity_state={"stress": index},
            package=primary,
            declaration=declaration,
            campaign_setting=sources.setting,
        )
        for index, (role, name) in enumerate((("player_character", "Vela"), ("companion", "Mora")))
    )
    return candidate, setting_id, plan, characters


@pytest.mark.parametrize("restore_interview", [False, True])
@pytest.mark.parametrize("required_party", [False, True])
def test_library_party_handoff_uses_declared_state_and_retains_profiles(
    tmp_path,
    restore_interview,
    required_party,
):
    database = tmp_path / "campaign.db"
    with AuthorityOwner.open(database, "library-seed") as owner:
        candidate, setting_id, plan, characters = seed_campaign_library(
            owner,
            tmp_path,
            required_party=required_party,
        )
    player_id = "captain" if required_party else characters[0].artifact_id
    companion_ids = (["guide"] if required_party else []) + [characters[1].artifact_id]
    harness = _OwnerHarness(database, None)
    try:
        options = harness.request(
            "party",
            "campaign_party_options",
            {
                "ruleset_selection": candidate.candidate_id,
                "setting_id": setting_id,
                "plan_id": plan.artifact_id,
            },
        )
        assert options["player_archetypes"][0]["id"] == player_id
        assert options["companions"][0]["campaign_ready"] is True
        if required_party:
            required_row = options["companions"][-1]
            assert required_row["campaign_ready"] is True
            assert required_row["source_ref"] == {"id": "guide", "path": "characters/guide.json"}
            assert required_row["reusable"] is False
            assert "authored_profile" not in required_row
            assert "entity_state" not in required_row
        draft = harness.request(
            "interview",
            "create_guided_campaign_draft",
            {
                "operation_id": "interview",
                "display_name": "Relay",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        if restore_interview:
            for revision, (method, extra) in enumerate(
                (
                    ("rename_campaign", {"display_name": "A later relay adventure"}),
                    ("archive_campaign", {"designated_session_id": draft["builder_session_id"]}),
                    ("restore_archived_campaign", {"session_id": draft["builder_session_id"]}),
                )
            ):
                harness.request(
                    method,
                    method,
                    {
                        "operation_id": method,
                        "campaign_id": draft["campaign_id"],
                        "expected_revision": revision,
                        **extra,
                    },
                )
        answers = (
            ("ruleset", {"candidate_id": candidate.candidate_id}),
            ("setting", {"setting_id": setting_id}),
            ("plan", {"plan_id": plan.artifact_id}),
            ("name", {"display_name": "Relay"}),
            (
                "participants",
                {
                    "archetype_id": player_id,
                    "companion_ids": companion_ids,
                },
            ),
            (
                "boundaries_preferences",
                {"boundaries": "No spiders", "preferences": "Quiet exploration"},
            ),
            ("specific_requests", {"text": "Start outside"}),
        )
        for revision, (step, answer) in enumerate(answers):
            harness.request(
                f"answer-{step}",
                "advance_guided_campaign_draft",
                {
                    "operation_id": f"answer-{step}",
                    "campaign_id": draft["campaign_id"],
                    "builder_session_id": draft["builder_session_id"],
                    "expected_construction_revision": revision,
                    "current_step": step,
                    "visible_answer": step,
                    "answer": answer,
                },
            )
        params = {
            "operation_id": "start-relay",
            "campaign_id": draft["campaign_id"],
            "builder_session_id": draft["builder_session_id"],
            "display_name": "Relay",
            "candidate_id": candidate.candidate_id,
            "setting_id": setting_id,
            "plan_id": plan.artifact_id,
            "player_character_id": player_id,
            "companion_ids": companion_ids,
            "premise": "",
            "construction_intent": {
                "boundaries": "No spiders",
                "preferences": "Quiet exploration",
                "specific_requests": "Start outside",
                "contributions": {},
            },
        }
        # A library choice may disappear or be damaged after the picker refreshes. Start must
        # fail before accepting Ready or binding any campaign state.
        profile_path = tmp_path / "participants" / characters[1].artifact_id / "profile.md"
        original_profile = profile_path.read_bytes()
        profile_path.write_bytes(original_profile + b"\nChanged after selection.")
        harness.send("stale-start", {"method": "start_guided_campaign", "params": params})
        rejected = harness.read()
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "campaign_library_selection_invalid"
        context = harness.request(
            "after-stale",
            "session_context",
            {
                "session_id": draft["builder_session_id"],
            },
        )
        assert not any(
            message["content"].get("interview_step") == "ready" for message in context["messages"]
        )
        assert not (tmp_path / "campaigns" / draft["campaign_id"] / "campaign-plan.json").exists()
        profile_path.write_bytes(original_profile)
        started = harness.request("start", "start_guided_campaign", params)
        assert harness.request("repeat-start", "start_guided_campaign", params) == started
        assert harness.request("resumed-start", "start_guided_campaign", {
            **params, "operation_id": "start-relay-after-restart",
        }) == started
        harness.send("changed-intent", {"method": "start_guided_campaign", "params": {
            **params,
            "operation_id": "start-relay-changed-intent",
            "construction_intent": {**params["construction_intent"], "boundaries": "Changed"},
        }})
        changed_intent = harness.read()
        assert changed_intent["ok"] is False
        assert "basis differs" in changed_intent["error"]["message"]
        harness.send(
            "changed-start",
            {
                "method": "start_guided_campaign",
                "params": {
                    **params,
                    "companion_ids": [],
                },
            },
        )
        assert harness.read()["ok"] is False
    finally:
        harness.close()
    with AuthorityOwner.open(database, "verify-start") as owner:
        campaign_id = draft["campaign_id"]
        snapshot = owner.snapshot(campaign_id)
        assert len(snapshot.participants) == (3 if required_party else 2)
        entities = snapshot.state[f"{characters[0].package_id}.state"]["entities"]
        assert entities["relay-keeper"] == {"stress": 4}
        world = snapshot.state[f"{characters[0].package_id}.state"]
        assert world["resources"] == {"charge": 8}
        source_scene = plan.plan.payload["initial_state"]["mechanics"][0]["value"]["spatial_scene"]
        assert (
            world["spatial_scene"]["entities"][: len(source_scene["entities"])]
            == source_scene["entities"]
        )
        assert len(world["spatial_scene"]["entities"]) == len(source_scene["entities"]) + len(
            snapshot.participants
        )
        assert set(world["eligible_actors"]) == {
            item.participant_id for item in snapshot.participants
        }
        rendered_map = project_tactical_grid({"scene": world["spatial_scene"]})
        assert rendered_map["map"]["id"] == "training-floor"
        assert [item["id"] for item in rendered_map["geometry"]] == ["door-a"]
        assert plan.plan.payload["initial_state"]["mechanics"][0]["value"]["entities"] == {
            "relay-keeper": {"stress": 4},
        }
        for participant in snapshot.participants:
            name = participant.data["name"]
            token = next(
                item
                for item in world["spatial_scene"]["entities"]
                if item["id"] == participant.participant_id
            )
            assert token["metadata"]["player"]["label"] == name
            entity = entities[participant.participant_id]
            assert entity["id"] == participant.participant_id
            assert entity["position_cell"] == [
                {
                    "Vela": 1,
                    "Captain": 1,
                    "Guide": 2,
                    "Mora": 3 if required_party else 2,
                }[name],
                1,
            ]
            assert token["position"][:2] == [
                coordinate + 0.5 for coordinate in entity["position_cell"]
            ]
            assert "party_role" not in participant.data
            assert (
                entities[participant.participant_id]["stress"]
                == {
                    "Vela": 0,
                    "Mora": 1,
                    "Captain": 11,
                    "Guide": 12,
                }[name]
            )
            reference = owner.read_campaign_reference(
                campaign_id,
                source="participant",
                path=participant.participant_id,
            )
            body = json.loads(reference["content"])
            assert body["profile"]["authored_text"] == f"# {name}\n\nTrained at the north relay."
            assert body["party_role"] == (
                "player_character" if name in {"Vela", "Captain"} else "companion"
            )
        plan_root = tmp_path / "campaigns" / campaign_id
        payload = json.loads((plan_root / "campaign-plan.json").read_text())
        assert payload["id"] == "relay-intrusion"
        assert "Player Boundary: No spiders" in payload["boundaries"]
        assert "Quiet exploration" in payload["gm_guidance"]["instructions"]
        assert "Start outside" in (plan_root / "campaign-plan.md").read_text()
        finalized = owner.finalize_campaign_construction(
            campaign_id=campaign_id,
            dm_provider_id="test.transport",
            dm_model_id="test.transport/model0",
            model_guidance_override=True,
            plan_artifact_text=(plan_root / "campaign-plan.md").read_bytes().decode("utf-8"),
            structured_plan=payload,
        )
        assert finalized[1].content["campaign_plan"]["id"] == "relay-intrusion"


def test_plan_world_rejects_duplicate_foreign_reserved_and_conflicting_state(tmp_path):
    with AuthorityOwner.open(tmp_path / "world.db", "world-owner") as owner:
        candidate, setting_id, plan, characters = seed_campaign_library(owner, tmp_path)
        selection = resolve_campaign_library_selection(
            owner,
            tmp_path,
            candidate_id=candidate.candidate_id,
            setting_id=setting_id,
            plan_id=plan.artifact_id,
        )
        for damage, message in (
            ("duplicate", "keys must be unique"),
            ("foreign", "must belong"),
            ("reserved", "reserved canonical channel"),
        ):
            payload = json.loads(canonical_json_bytes(plan.plan.payload))
            entries = payload["initial_state"]["mechanics"]
            if damage == "duplicate":
                entries.append(entries[0])
            elif damage == "foreign":
                entries[0]["namespace"] = "another.ruleset"
            else:
                entries[0]["value"] = {"private_reasoning": "forbidden structural channel"}
            with pytest.raises(ValueError, match=message):
                selection.sources.validate(payload, plan.plan.readable_markdown)

        from sylvia_gm.application.campaign_starting_world import materialize_starting_world

        with pytest.raises(CommandRejected, match="conflicting identities"):
            materialize_starting_world(
                plan.plan,
                entity_reference=selection.entity_reference,
                party_entities={"relay-keeper": {"stress": 99}},
            )
        payload = json.loads(canonical_json_bytes(plan.plan.payload))
        payload["initial_state"]["mechanics"][0]["value"]["entities"] = []
        malformed = selection.sources.validate(payload, plan.plan.readable_markdown)
        with pytest.raises(CommandRejected, match="must be an object"):
            materialize_starting_world(
                malformed,
                entity_reference=selection.entity_reference,
                party_entities={characters[0].artifact_id: {"stress": 0}},
            )


def test_party_selection_rechecks_roles_limits_and_unavailable_sources(tmp_path):
    with AuthorityOwner.open(tmp_path / "selection.db", "selection-owner") as owner:
        candidate, setting_id, plan, characters = seed_campaign_library(owner, tmp_path)
        selection = resolve_campaign_library_selection(
            owner,
            tmp_path,
            candidate_id=candidate.candidate_id,
            setting_id=setting_id,
            plan_id=plan.artifact_id,
        )
        logical_selection = resolve_campaign_library_selection(
            owner,
            tmp_path,
            candidate_id=characters[0].package_id,
            setting_id=setting_id,
            plan_id=plan.artifact_id,
        )
        assert logical_selection.candidate == candidate
        for pc, companions in (
            (characters[1].artifact_id, ()),
            (characters[0].artifact_id, (characters[0].artifact_id,)),
            (characters[0].artifact_id, (characters[1].artifact_id,) * 2),
            ("missing-character", ()),
        ):
            with pytest.raises(CommandRejected):
                materialize_library_party(
                    owner,
                    selection,
                    operation_id="bad-party",
                    player_character_id=pc,
                    companion_ids=companions,
                )
        missing_location = replace(selection, entity_reference=None)
        assert missing_location.party_options()["player_archetypes"][0]["campaign_ready"] is False
        with pytest.raises(CommandRejected, match="state location"):
            materialize_library_party(
                owner,
                missing_location,
                operation_id="missing-location",
                player_character_id=characters[0].artifact_id,
                companion_ids=(),
            )
        payload = json.loads(canonical_json_bytes(plan.plan.payload))
        payload["starting_party"]["player_character"] = {
            "mode": "fixed",
            "character": {"id": "operator", "path": "characters/operator.md"},
        }
        fixed = replace(
            selection,
            plan=replace(
                plan,
                plan=selection.sources.validate(
                    payload,
                    plan.plan.readable_markdown,
                ),
            ),
        )
        fixed_row = fixed.party_options()["player_archetypes"][-1]
        assert fixed_row["campaign_ready"] is False
        assert fixed_row["source_ref"]["path"] == "characters/operator.md"
        with pytest.raises(CommandRejected, match="required Setting character"):
            materialize_library_party(
                owner,
                fixed,
                operation_id="fixed-missing",
                player_character_id="operator",
                companion_ids=(),
            )
        with pytest.raises(CommandRejected):
            resolve_campaign_library_selection(
                owner,
                tmp_path,
                candidate_id=candidate.candidate_id,
                setting_id="another-setting",
                plan_id=plan.artifact_id,
            )


def test_required_character_selection_enforces_fixed_identity_and_custom_limits(tmp_path):
    with AuthorityOwner.open(tmp_path / "selection.db", "required-selection") as owner:
        candidate, setting_id, plan, characters = seed_campaign_library(
            owner,
            tmp_path,
            required_party=True,
        )
        selection = resolve_campaign_library_selection(
            owner,
            tmp_path,
            candidate_id=candidate.candidate_id,
            setting_id=setting_id,
            plan_id=plan.artifact_id,
        )
        for player_id, companions in (
            (characters[0].artifact_id, ("guide",)),
            ("captain", ()),
            ("captain", (characters[1].artifact_id,)),
            ("captain", ("guide", "guide")),
        ):
            with pytest.raises(CommandRejected):
                materialize_library_party(
                    owner,
                    selection,
                    operation_id="invalid-required-party",
                    player_character_id=player_id,
                    companion_ids=companions,
                )
        payload = json.loads(canonical_json_bytes(plan.plan.payload))
        payload["starting_party"]["custom_companions"] = {"minimum": 0, "maximum": 0}
        required_only = replace(
            selection,
            plan=replace(
                plan,
                plan=selection.sources.validate(payload, plan.plan.readable_markdown),
            ),
        )
        participants, _, profiles = materialize_library_party(
            owner,
            required_only,
            operation_id="required-party",
            player_character_id="captain",
            companion_ids=("guide",),
        )
        assert [item.data["name"] for item in participants] == ["Captain", "Guide"]
        assert [item["character_file"]["source_id"] for item in profiles] == ["captain", "guide"]
        with pytest.raises(CommandRejected, match="starting party policy"):
            materialize_library_party(
                owner,
                required_only,
                operation_id="extra-custom",
                player_character_id="captain",
                companion_ids=("guide", characters[1].artifact_id),
            )


def test_incomplete_packaged_character_stays_unavailable(tmp_path):
    with AuthorityOwner.open(tmp_path / "selection.db", "incomplete-selection") as owner:
        candidate, setting_id = install_character_packages(
            owner,
            setting_characters=(
                {
                    "id": "unfinished",
                    "summary": "An unfinished operator.",
                    "authored_profile": "",
                    "participant": {"name": "Unfinished", "callsign": "Relay"},
                    "entity_state": {"stress": 0},
                },
            ),
        )
        sources = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting_id)
        payload = json.loads(_campaign_plan_example(sources))
        payload["starting_party"]["player_character"] = {
            "mode": "fixed",
            "character": {"id": "unfinished", "path": "characters/unfinished.json"},
        }
        plan = save_reusable_campaign_plan(
            tmp_path,
            operation_id="unfinished-plan",
            source_payload=payload,
            source_markdown="# Unfinished operator",
            validate_plan=sources.validate,
        )
        selection = resolve_campaign_library_selection(
            owner,
            tmp_path,
            candidate_id=candidate.candidate_id,
            setting_id=setting_id,
            plan_id=plan.artifact_id,
        )
        assert selection.party_options()["player_archetypes"][0]["campaign_ready"] is False
        with pytest.raises(CommandRejected, match="required Setting character"):
            materialize_library_party(
                owner,
                selection,
                operation_id="unfinished-party",
                player_character_id="unfinished",
                companion_ids=(),
            )
