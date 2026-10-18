"""Character bookkeeping uses the shared Campaign File interface and canonical state."""

import io
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics, _campaign_file_updates
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.domain.authority import PackageLock, StateValue, WriterRole, owned_payload
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, MechanicsRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from tests.test_srd_second_wind import _installed_fighter


def test_no_rules_people_and_objects_remain_durable_campaign_state(tmp_path: Path) -> None:
    """Generic campaign memory must not depend on declared mechanical fields."""
    database = tmp_path / "no-rules-memory.db"
    campaign_id = "no-rules-memory"
    with AuthorityOwner.open(database, "owner-no-rules-memory") as owner:
        owner.create_campaign(
            CampaignCreate(
                campaign_id=campaign_id,
                display_name="No-rules memory",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="play",
            )
        )
        session = owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.DM,
            provider_id="scripted",
            model_id="scripted",
        )
        owner.append_agent_message(
            session_id=session.session_id,
            message_id="meet-keeper",
            kind=SessionMessageKind.USER,
            content={"text": "I ask the keeper about the lantern."},
        )
        mcp = StatelessMcpMechanics(owner)
        result = mcp.commit_campaign_file(
            campaign_id,
            session_id=session.session_id,
            basis_message_id="meet-keeper",
            parameters={
                "state_change": "changed",
                "state_change_summary": "The keeper and lantern are now established.",
                "entities": [
                    {
                        "entity_id": "keeper",
                        "entity_kind": "person",
                        "name": "Keeper",
                        "attributes": {"personality": "patient"},
                        "visibility": "player_known",
                        "known_by": ["player"],
                    },
                    {
                        "entity_id": "lantern",
                        "entity_kind": "object",
                        "name": "Blue lantern",
                        "attributes": {"description": "A blue glass lantern beside the keeper."},
                        "visibility": "player_known",
                        "known_by": ["player"],
                    },
                ],
            },
        )
        assert result["status"] == "campaign_file_updated"
        expected = owner.snapshot(campaign_id)
        assert not expected.package_lock.entries
    with AuthorityOwner.open(database, "owner-no-rules-reopened") as owner:
        assert owner.snapshot(campaign_id) == expected
        for identity, detail in (("keeper", "patient"), ("lantern", "blue glass lantern")):
            reference = owner.read_campaign_reference(
                campaign_id,
                source="campaign_state",
                path=identity,
            )
            assert detail in reference["content"]


def test_shared_character_update_changes_canonical_carried_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = LocalOwnerSupervisor()
    issued = supervisor.acquire_owner(tmp_path / "bookkeeping.db", "owner-bookkeeping")
    supervisor.complete_handshake(derive_owner_response(issued.secret_hex))
    owner = supervisor.application_owner()
    try:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="bookkeeping-srd",
        )
        template = _installed_fighter(candidate.package_lock, fighter=True)
        player = replace(
            template,
            participant_id="tessa",
            data=owned_payload(
                {
                    **template.data,
                    "name": "Tessa",
                }
            ),
        )
        companion = replace(
            template,
            participant_id="mira",
            data=owned_payload(
                {
                    **template.data,
                    "name": "Mira",
                    "party_role": "companion",
                }
            ),
        )
        initial_entity = {"hp": 10, "max_hp": 10, "inventory": ["chain_mail", "greatsword"]}
        owner.create_campaign(
            CampaignCreate(
                campaign_id="bookkeeping-party",
                display_name="Synthetic bookkeeping party",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="bookkeeping-turn",
                participants=(player, companion),
                initial_state=(
                    StateValue("sylvia.srd-lite.bg3", "combat", {"encounter_active": False}),
                    StateValue(
                        "sylvia.srd-lite.bg3",
                        "entities",
                        owned_payload(
                            {
                                "tessa": {**initial_entity, "name": "Tessa"},
                                "mira": {
                                    **initial_entity,
                                    "name": "Mira",
                                    "party_role": "companion",
                                },
                            }
                        ),
                    ),
                ),
            )
        )
        campaign_id = "bookkeeping-party"
        session_id = owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.DM,
            provider_id="scripted",
            model_id="scripted",
        ).session_id
        owner.append_agent_message(
            session_id=session_id,
            message_id="borrow-equipment",
            kind=SessionMessageKind.USER,
            content={"text": "I accept the quartermaster's loan of a two-person tent."},
        )
        before = owner.snapshot(campaign_id)
        actor_id = "tessa"
        entities = before.state["sylvia.srd-lite.bg3.entities"]
        original = entities[actor_id]
        inventory = list(original["inventory"])
        assert "two_person_tent" not in inventory
        inventory.append("two_person_tent")
        mcp = StatelessMcpMechanics(owner)
        tools = mcp.list_tools(
            campaign_id,
            purpose="bookkeeping-regression",
            discovery_id="bookkeeping-discovery",
            session_id=session_id,
        )
        shared_update = next(tool for tool in tools if tool.name == "update_campaign_file")
        assert not any(tool.name == "update_srd_carried_inventory" for tool in tools)
        assert "character_updates" in shared_update.input_schema["properties"], (
            "The shared update tool must expose validated character bookkeeping"
        )
        assert "inventory_updates" not in shared_update.input_schema["properties"]
        panels = owner.campaign_panels(campaign_id)["surfaces"]
        assert any(panel["id"] == "dnd-inventory" for panel in panels)
        assert not any(panel["renderer"] == "generic_inventory" for panel in panels)
        surfaces = owner._store.package_surfaces_for_current_lock

        def without_specialised_inventory(campaign):
            prompts, ui = surfaces(campaign)
            return prompts, tuple(surface for surface in ui if surface.identity != "dnd-inventory")

        with monkeypatch.context() as patch:
            patch.setattr(
                owner._store, "package_surfaces_for_current_lock", without_specialised_inventory
            )
            generic = next(panel for panel in owner.campaign_panels(campaign_id)["surfaces"]
                           if panel["renderer"] == "generic_inventory")
        tessa = next(item for item in generic["data"]["characters"] if item["id"] == "tessa")
        assert [item["name"] for item in tessa["items"]] == list(original["inventory"])
        assert [item["item_id"] for item in tessa["items"]] == list(original["inventory"])
        with pytest.raises(CommandRejected, match="active Ruleset inventory"):
            owner.update_campaign_file(
                session_id=session_id, update_id="forbidden-fallback",
                updates=[{"operation": "update_inventory", "expected_revision": before.revision}],
                basis_message_ids=("borrow-equipment",),
            )
        assert owner.snapshot(campaign_id) == before
        parameters = {
            "state_change": "changed",
            "state_change_summary": "The character carries a borrowed tent.",
            "character_updates": [
                {
                    "participant_id": actor_id,
                    "expected_revision": before.revision,
                    "fields": {"inventory": inventory},
                }
            ],
            "facts": [
                {
                    "fact_id": "quartermaster-tent-loan",
                    "category": "continuity",
                    "text": "The tent belongs to Waystation and must be returned tomorrow.",
                    "visibility": "player_known",
                    "known_by": ["player", actor_id],
                }
            ],
        }

        def commit(parameters):
            return mcp.commit_campaign_file(
                campaign_id,
                session_id=session_id,
                basis_message_id="borrow-equipment",
                parameters=parameters,
            )

        for invalid in (
            {"participant_id": "unavailable-character"},
            {"fields": {"hp": 900}},
            {"fields": {"inventory": ["tent", "tent"]}},
            {"fields": {"inventory": "tent"}},
            {"expected_revision": before.revision - 1},
        ):
            rejected = deepcopy(parameters)
            rejected["character_updates"][0].update(invalid)
            with pytest.raises(MechanicsRejected):
                commit(rejected)
            assert owner.snapshot(campaign_id) == before

        partial = deepcopy(parameters)
        partial["character_updates"].append(
            {
                "participant_id": "mira",
                "expected_revision": before.revision,
                "fields": {"inventory": 42},
            }
        )
        with pytest.raises(MechanicsRejected):
            commit(partial)
        assert owner.snapshot(campaign_id) == before

        disguised = deepcopy(parameters)
        disguised.pop("character_updates")
        disguised["entities"] = [
            {
                "entity_id": actor_id,
                "entity_kind": "person",
                "name": "Tessa",
                "attributes": {"inventory": inventory},
                "visibility": "player_known",
                "known_by": ["player", actor_id],
            }
        ]
        with pytest.raises(MechanicsRejected) as narrative_write:
            commit(disguised)
        assert narrative_write.value.code == "character_fields_require_bookkeeping"
        assert owner.snapshot(campaign_id) == before

        def refuse_commit():
            raise OSError("Synthetic storage failure")

        with monkeypatch.context() as patch:
            patch.setattr(owner._store, "_before_commit", refuse_commit)
            with pytest.raises(MechanicsRejected) as failed_save:
                commit(parameters)
            assert failed_save.value.code == "campaign_file_save_failed"
        assert owner.snapshot(campaign_id) == before

        result = commit(parameters)
        assert result["status"] == "campaign_file_updated"
        bookkeeping = next(
            item for item in owner.player_projection(campaign_id)
            if item.safe_payload.get("tool_name") == "update_srd_carried_inventory"
        )
        assert bookkeeping.safe_payload["routine_activity"] == "character_update"
        paged_bookkeeping = next(
            item for item in owner.player_projection_page(
                campaign_id, after_sequence=bookkeeping.sequence - 1,
            )
            if item.sequence == bookkeeping.sequence
        )
        assert paged_bookkeeping.safe_payload["routine_activity"] == "character_update"
        after = owner.snapshot(campaign_id)
        updated = after.state["sylvia.srd-lite.bg3.entities"][actor_id]
        assert list(updated["inventory"]) == inventory
        assert {key: value for key, value in updated.items() if key != "inventory"} == {
            key: value for key, value in original.items() if key != "inventory"
        }
        assert any(
            record.record_id == "quartermaster-tent-loan" for record in after.continuity.records
        )
        assert commit(parameters) == result
        assert owner.snapshot(campaign_id) == after
        conflicting = deepcopy(parameters)
        conflicting["facts"][0]["text"] = "The tent is now a gift."
        with pytest.raises(IdempotencyConflict):
            owner.update_campaign_file(
                session_id=session_id,
                update_id=result["operation_id"],
                updates=_campaign_file_updates(
                    conflicting,
                    owner.campaign_narrative_options(session_id),
                ),
                basis_message_ids=("borrow-equipment",),
            )
        assert owner.snapshot(campaign_id) == after
        reference = json.loads(
            owner.read_campaign_reference(
                campaign_id,
                source="participant",
                path=actor_id,
            )["content"]
        )
        assert reference["current_state"]["fields"]["inventory"] == inventory
        assert reference["current_state"]["campaign_revision"] == after.revision
        assert reference["ruleset"]["starting_data"] == dict(player.data)

        # A purchase debits coins and grants items in one field replacement, not notes alone.
        holdings = [item for item in inventory if not item.endswith(("_gp", "_sp", "_cp"))]
        funded = deepcopy(parameters)
        funded.pop("facts")
        funded["character_updates"][0].update(
            {
                "expected_revision": after.revision,
                "fields": {"inventory": [*holdings, "53_gp"]},
            }
        )
        commit(funded)
        purchase = deepcopy(funded)
        purchase["character_updates"][0].update(
            {
                "expected_revision": owner.snapshot(campaign_id).revision,
                "fields": {"inventory": [*holdings, "bedroll", "blanket", "49_gp", "5_sp"]},
            }
        )
        purchase_result = commit(purchase)
        purchased = owner.snapshot(campaign_id)
        assert (
            list(purchased.state["sylvia.srd-lite.bg3.entities"][actor_id]["inventory"])
            == (purchase["character_updates"][0]["fields"]["inventory"])
        )
        assert commit(purchase) == purchase_result
        assert owner.snapshot(campaign_id) == purchased
        inventory_panel = next(
            item
            for item in owner.campaign_panels(campaign_id)["surfaces"]
            if item["id"] == "dnd-inventory"
        )
        assert (
            list(inventory_panel["data"]["entities"][actor_id]["inventory"])
            == (purchase["character_updates"][0]["fields"]["inventory"])
        )
        mira_before = purchased.state["sylvia.srd-lite.bg3.entities"]["mira"]
        loan = deepcopy(parameters)
        loan["character_updates"][0].update(
            {
                "participant_id": "mira",
                "expected_revision": purchased.revision,
                "fields": {"inventory": [*mira_before["inventory"], "bedroll", "blanket"]},
            }
        )
        loan["facts"][0].update(
            {
                "fact_id": "mira-bedding-loan",
                "known_by": ["player", "mira"],
                "text": "Mira carries Waystation's bedroll and blanket on loan, due tomorrow.",
            }
        )
        loan["entities"] = [
            {
                "entity_id": "mira",
                "entity_kind": "person",
                "name": "Mira",
                "attributes": {"personality": "thoughtful", "background": "traveller"},
                "visibility": "player_known",
                "known_by": ["player", "mira"],
            }
        ]
        commit(loan)
        returning = deepcopy(loan)
        returning["character_updates"][0].update(
            {
                "expected_revision": owner.snapshot(campaign_id).revision,
                "fields": {"inventory": list(mira_before["inventory"])},
            }
        )
        returning["facts"][0]["text"] = "Mira returned Waystation's bedroll and blanket."
        commit(returning)
        returned = owner.snapshot(campaign_id)
        assert returned.state["sylvia.srd-lite.bg3.entities"]["mira"] == mira_before
        assert (
            "thoughtful"
            in owner.read_campaign_reference(
                campaign_id,
                source="campaign_state",
                path="mira",
            )["content"]
        )
        assert returned.participants == before.participants
        supervisor.shutdown()
        with AuthorityOwner.open(
            tmp_path / "bookkeeping.db", "owner-bookkeeping-reopened"
        ) as reopened:
            assert reopened.snapshot(campaign_id) == returned
    finally:
        supervisor.shutdown()
