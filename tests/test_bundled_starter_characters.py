"""Concrete starters use the actual bundled declarations and ordinary campaign boundaries."""

import json
from copy import deepcopy
from dataclasses import replace
from importlib.resources import files

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_campaign_plans import bundled_plan_catalog
from sylvia_gm.application.bundled_starter_characters import bundled_starter_participants
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.compatible_character_copy import (
    authored_character_state,
    copy_compatible_character,
)
from sylvia_gm.application.initial_character_state import verify_initial_character_state
from sylvia_gm.application.reusable_participants import (
    delete_reusable_participant,
    load_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.domain.authority import PackageRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

CLASSES = (
    "barbarian",
    "bard",
    "cleric",
    "druid",
    "fighter",
    "monk",
    "paladin",
    "ranger",
    "rogue",
    "sorcerer",
    "warlock",
    "wizard",
)
EXPECTED = (
    (14, 14),
    (9, 13),
    (9, 17),
    (9, 15),
    (12, 17),
    (10, 15),
    (12, 18),
    (12, 15),
    (10, 14),
    (7, 12),
    (9, 13),
    (8, 12),
)


def selection(owner, root, plan_id="last-cart-through"):
    return resolve_campaign_library_selection(
        owner,
        root,
        candidate_id="sylvia.srd-lite.bg3",
        setting_id="pneuma.severed-lands",
        plan_id=plan_id,
    )


def authority(owner, selected):
    primary = next(
        p for p in selected.candidate.package_lock.entries if p.role is PackageRole.PRIMARY
    )
    declaration = next(
        d
        for d in owner.exact_participant_declarations(
            primary.package_id,
            primary.version,
            primary.sha256,
        )
        if d.kind == "player_character"
    )
    return primary, declaration


def test_twelve_concrete_builds_publish_calculate_materialize_and_keep_sources(tmp_path):
    source_path = files("sylvia_gm.campaign").joinpath("srd-starters.json")
    original = source_path.read_bytes()
    with AuthorityOwner.open(tmp_path / "owner.db", "starters") as owner:
        selected = selection(owner, tmp_path)
        package, declaration = authority(owner, selected)
        starters = tuple(p for p in selected.participants if p.builtin_starter)
        assert len(starters) == 12
        assert tuple(p.participant["classes"][0]["id"] for p in starters) == CLASSES
        rows = selected.party_options()["player_archetypes"]
        for item, (hp, ac), row in zip(starters, EXPECTED, rows, strict=True):
            assert row["builtin_starter"] is True and row["campaign_ready"] is True
            assert row["starter_preview"]["entity_state"] == item.entity_state
            assert "Level 1" in row["summary"]
            assert "starter_preview" not in item.catalog_payload()  # Discovery stays compact.
            assert item.participant["classes"] == [
                {"id": item.participant["classes"][0]["id"], "level": 1}
            ]
            assert not item.participant.get("subclasses")
            state = item.entity_state
            assert (state["hp"], state["armor_class"], state["speed"]) == (hp, ac, 30)
            assert state["resources"]["hit_die"] == 1
            assert "focus_point" not in state["resources"]
            assert "wild_shape" not in state["resources"]
            assert "action_surge" not in state["resources"]
            if item.participant["classes"][0]["id"] in ("barbarian", "monk"):
                assert state["worn_armor"] is None
            verified = verify_initial_character_state(
                item.participant,
                state,
                entity_state_schema=declaration.entity_state_schema,
                initial_state_calculations=declaration.initial_state_calculations,
                initial_state_validation=declaration.initial_state_validation,
                setting_data=item.setting_data,
            )
            assert canonical_json_bytes(verified) == canonical_json_bytes(state)
            before = canonical_json_bytes(item.rift_source())
            party, values, _ = materialize_library_party(
                owner,
                selected,
                operation_id=item.artifact_id,
                player_character_id=item.artifact_id,
                companion_ids=(),
            )
            entities = next(v.value for v in values if v.key == "entities")
            actor = entities[party[0].participant_id]
            assert actor["max_hp"] == hp and actor["armor_class"] == ac
            assert actor["prepared_spells"] == state["prepared_spells"]
            assert canonical_json_bytes(item.rift_source()) == before
        by_class = dict(zip(CLASSES, starters, strict=True))
        assert by_class["barbarian"].entity_state["resources"]["rage"] == 2
        assert by_class["bard"].entity_state["resources"]["bardic_inspiration"] == 3
        assert by_class["fighter"].entity_state["resources"]["second_wind"] == 2
        assert by_class["paladin"].entity_state["resources"]["lay_on_hands"] == 5
        assert by_class["sorcerer"].entity_state["resources"]["innate_sorcery"] == 2
        assert by_class["warlock"].entity_state["resources"]["spell_slot_1"] == 1
        assert by_class["wizard"].entity_state["resources"]["arcane_recovery"] == 1
        assert len(by_class["wizard"].participant["wizard_spellbook"]) == 6
        assert all(p.entity_state["inventory_quantities"]["gp"] > 0 for p in starters)
        # Every authored field goes through ordinary publication into a distinct editable copy.
        source = by_class["wizard"]
        edited = deepcopy(source.participant)
        edited.update(name="Eli Revised", abilities={**edited["abilities"], "intelligence": 16})
        saved = save_reusable_participant(
            tmp_path,
            operation_id="edited-copy",
            role="player_character",
            summary="A revised wizard.",
            authored_profile="A new personality and player-chosen history.",
            participant=edited,
            entity_state=authored_character_state(source.entity_state, declaration),
            package=package,
            declaration=declaration,
            campaign_setting=selected.sources.setting,
        )
        assert not saved.builtin_starter and saved.artifact_id != source.artifact_id
        assert saved.participant["name"] == "Eli Revised"
        assert (
            delete_reusable_participant(
                tmp_path,
                source.artifact_id,
                expected_content_sha256=source.content_sha256,
                confirmation=f"permanently delete {source.artifact_id}",
            )
            is False
        )
    with AuthorityOwner.open(tmp_path / "owner.db", "reopened") as owner:
        reopened = selection(owner, tmp_path)
        assert tuple(p.content_sha256 for p in reopened.participants if p.builtin_starter) == tuple(
            p.content_sha256 for p in starters
        )
        restored = load_reusable_participant(
            tmp_path,
            saved.artifact_id,
            package=package,
            declaration=declaration,
            campaign_setting=reopened.sources.setting,
        )
        assert restored == saved
    assert source_path.read_bytes() == original


def test_all_bundled_plan_policies_and_exact_setting_snapshots(tmp_path):
    seen = set()
    with AuthorityOwner.open(tmp_path / "policies.db", "policies") as owner:
        for row in bundled_plan_catalog():
            if row["ruleset"]["id"] != "sylvia.srd-lite.bg3":
                continue
            selected = selection(owner, tmp_path, row["id"])
            options = selected.party_options()
            assert options["starting_party"] == selected.plan.plan.payload["starting_party"]
            starters = tuple(p for p in selected.participants if p.builtin_starter)
            assert len(starters) == 12
            key = selected.sources.setting.content_sha256
            if key not in seen:
                # Each retained Setting variant really admits every build, not just a name match.
                for item in starters:
                    assert item.campaign_setting_sha256 == key
                seen.add(key)
            policy = options["starting_party"]
            required = tuple(c["id"] for c in policy["required_companions"])
            minimum = policy["custom_companions"]["minimum"]
            package, declaration = authority(owner, selected)
            custom = []
            for index in range(minimum):
                source = starters[0]
                saved = save_reusable_participant(
                    tmp_path,
                    operation_id=f"{row['id']}-helper-{index}",
                    role="companion",
                    summary="A synthetic companion for this party-policy check.",
                    authored_profile="A synthetic helper with no campaign history.",
                    participant={**source.participant, "name": f"Helper {index}"},
                    entity_state=authored_character_state(source.entity_state, declaration),
                    package=package,
                    declaration=declaration,
                    campaign_setting=selected.sources.setting,
                )
                custom.append(saved.artifact_id)
            if custom:
                selected = selection(owner, tmp_path, row["id"])
            companions = (*required, *custom)
            player = starters[0].artifact_id
            if policy["player_character"]["mode"] == "fixed":
                with pytest.raises(CommandRejected):
                    materialize_library_party(
                        owner,
                        selected,
                        operation_id="wrong-fixed",
                        player_character_id=starters[0].artifact_id,
                        companion_ids=required,
                    )
                player = policy["player_character"]["character"]["id"]
            else:
                if required:
                    with pytest.raises(CommandRejected):
                        materialize_library_party(
                            owner,
                            selected,
                            operation_id="missing-required",
                            player_character_id=starters[0].artifact_id,
                            companion_ids=(),
                        )
            materialize_library_party(
                owner,
                selected,
                operation_id="policy-" + row["id"],
                player_character_id=player,
                companion_ids=companions,
            )
            with pytest.raises(CommandRejected):
                materialize_library_party(
                    owner,
                    selected,
                    operation_id="extra-companion",
                    player_character_id=player,
                    companion_ids=required
                    + tuple(
                        f"extra-{i}" for i in range(policy["custom_companions"]["maximum"] + 1)
                    ),
                )
    assert seen


def test_starters_elsewhere_require_explicit_compatible_copy_or_adaptation(tmp_path):
    with AuthorityOwner.open(tmp_path / "copy.db", "copy") as owner:
        selected = selection(owner, tmp_path)
        package, declaration = authority(owner, selected)
        original = selected.participants[0]
        destination = replace(selected.sources.setting, package_id="example.other-world")
        assert (
            bundled_starter_participants(
                tmp_path, package=package, declaration=declaration, campaign_setting=destination
            )
            == ()
        )
        copied = copy_compatible_character(
            tmp_path,
            source=original,
            expected_content_sha256=original.content_sha256,
            operation_id="explicit-copy",
            package=package,
            declaration=declaration,
            campaign_setting=destination,
        )
        assert copied.participant == original.participant
        assert copied.campaign_setting_id == "example.other-world"
        assert copied.artifact_id != original.artifact_id and not copied.builtin_starter
        with pytest.raises(CommandRejected) as rejected:
            copy_compatible_character(
                tmp_path,
                source=original,
                expected_content_sha256=original.content_sha256,
                operation_id="needs-adaptation",
                package=package,
                declaration=declaration,
                campaign_setting=destination,
                setting_character_schema={
                    "type": "object",
                    "properties": {"home": {"type": "string"}},
                    "required": ["home"],
                    "additionalProperties": False,
                },
            )
        assert rejected.value.code == "character_adaptation_required"
        assert "explicit" in rejected.value.diagnostics[0].guidance
        # Builder's ordinary publication path supplies the required destination facts on a copy.
        adapted = save_reusable_participant(
            tmp_path,
            operation_id="explicit-adaptation",
            role="player_character",
            summary=original.summary,
            authored_profile=original.authored_profile,
            participant=original.participant,
            entity_state=authored_character_state(original.entity_state, declaration),
            package=package,
            declaration=declaration,
            campaign_setting=destination,
            setting_data={"home": "The player chose Harbor"},
            setting_character_schema={
                "type": "object",
                "properties": {"home": {"type": "string"}},
                "required": ["home"],
                "additionalProperties": False,
            },
        )
        assert adapted.setting_data["home"] == "The player chose Harbor"
        assert original.setting_data == {}


def test_all_twelve_start_save_and_reopen_through_owner_protocol(tmp_path):
    database = tmp_path / "journey.db"
    harness = _OwnerHarness(database, None)
    campaigns = []
    try:
        options = harness.request(
            "party",
            "campaign_party_options",
            {
                "ruleset_selection": "sylvia.srd-lite.bg3",
                "setting_id": "pneuma.severed-lands",
                "plan_id": "last-cart-through",
            },
        )
        rows = [row for row in options["player_archetypes"] if row.get("builtin_starter")]
        assert len(rows) == 12
        for index, row in enumerate(rows):
            suffix = str(index)
            draft = harness.request(
                "draft-" + suffix,
                "create_guided_campaign_draft",
                {
                    "operation_id": "draft-" + suffix,
                    "display_name": row["name"],
                    "provider_id": "test.transport",
                    "model_id": "test.transport/model0",
                },
            )
            answers = (
                ("ruleset", {"candidate_id": "sylvia.srd-lite.bg3"}),
                ("setting", {"setting_id": "pneuma.severed-lands"}),
                ("plan", {"plan_id": "last-cart-through"}),
                ("name", {"display_name": row["name"]}),
                ("participants", {"archetype_id": row["id"], "companion_ids": []}),
                ("boundaries_preferences", {"boundaries": "", "preferences": ""}),
                ("specific_requests", {"text": "No additional requests"}),
            )
            for revision, (step, answer) in enumerate(answers):
                harness.request(
                    step + suffix,
                    "advance_guided_campaign_draft",
                    {
                        "operation_id": step + suffix,
                        "campaign_id": draft["campaign_id"],
                        "builder_session_id": draft["builder_session_id"],
                        "expected_construction_revision": revision,
                        "current_step": step,
                        "visible_answer": step,
                        "answer": answer,
                    },
                )
            harness.request(
                "start-" + suffix,
                "start_guided_campaign",
                {
                    "operation_id": "start-" + suffix,
                    "campaign_id": draft["campaign_id"],
                    "builder_session_id": draft["builder_session_id"],
                    "display_name": row["name"],
                    "candidate_id": "sylvia.srd-lite.bg3",
                    "setting_id": "pneuma.severed-lands",
                    "plan_id": "last-cart-through",
                    "player_character_id": row["id"],
                    "companion_ids": [],
                    "premise": "",
                    "construction_intent": {
                        "boundaries": "",
                        "preferences": "",
                        "specific_requests": "No additional requests",
                        "contributions": {},
                    },
                },
            )
            campaigns.append((draft["campaign_id"], row))
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopened-journeys") as owner:
        for campaign_id, row in campaigns:
            plan_root = tmp_path / "campaigns" / campaign_id
            owner.finalize_campaign_construction(
                campaign_id=campaign_id,
                dm_provider_id="test.provider",
                dm_model_id="test/model",
                model_guidance_override=True,
                plan_artifact_text=(plan_root / "campaign-plan.md").read_text(),
                structured_plan=json.loads((plan_root / "campaign-plan.json").read_text()),
            )
            snapshot = owner.snapshot(campaign_id)
            assert len(snapshot.participants) == 1
            pc = snapshot.participants[0]
            assert pc.data["name"] == row["name"]
            actor = snapshot.state["sylvia.srd-lite.bg3.entities"][pc.participant_id]
            assert actor["max_hp"] == row["starter_preview"]["entity_state"]["max_hp"]
            reference = json.loads(
                owner.read_campaign_reference(
                    campaign_id,
                    source="participant",
                    path=pc.participant_id,
                )["content"]
            )
            assert (
                reference["profile"]["authored_text"] == row["starter_preview"]["authored_profile"]
            )
