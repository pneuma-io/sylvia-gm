"""Fallback inventory commits through the real owner, MCP and SQLite boundaries."""

import json
from copy import deepcopy

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.errors import IdempotencyConflict, MechanicsRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure import sqlite_continuity
from tests.test_inventory import holding
from tests.test_panel_starting_layout import compile_layout, starting_layout


def entity(identity, kind="person"):
    return {
        "entity_id": identity,
        "entity_kind": kind,
        "name": identity.title(),
        "attributes": {},
        "visibility": "player_known",
        "known_by": ["player"],
    }


@pytest.mark.parametrize("renderer", ["inventory", "key_value"])
def test_specialised_inventory_panel_replaces_generic_without_new_binding(tmp_path, renderer):
    """Old pinned packages declare an inventory renderer even without bookkeeping annotations."""
    database = tmp_path / "panels.db"
    with AuthorityOwner.open(database, "panels") as owner:
        compiled = compile_layout(starting_layout(), inventory_renderer=renderer)
        with materialize_package_archive((compiled.draft,)) as artifact:
            candidate = owner.install_package_archive(artifact.archive, install_id="panels")
        owner.create_campaign(CampaignCreate(
            campaign_id="panels", display_name="Panel precedence", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="play",
        ))
        projection = owner.campaign_panels("panels")
        assert sum(p["renderer"] == "generic_inventory" for p in projection["surfaces"]) == (
            0 if renderer == "inventory" else 1
        )
        assert any(p["id"] == "inventory" for p in projection["surfaces"])
    with AuthorityOwner.open(database, "panels-reopen") as owner:
        assert owner.campaign_panels("panels") == projection


@pytest.mark.parametrize("kind", ["person", "character", "player_character", "companion", "npc"])
def test_public_inventory_uses_visibility_not_character_knowledge(tmp_path, kind):
    """Accepted No Ruleset people and holdings stay visible across reopen without a new write."""
    database = tmp_path / "public-inventory.db"
    with AuthorityOwner.open(database, "public-inventory") as owner:
        owner.create_campaign(CampaignCreate(
            campaign_id="public", display_name="Public inventory", writer=WriterRole.DM,
            package_lock=PackageLock(entries=()), turn_id="play",
        ))
        session = owner.create_agent_session(
            campaign_id="public", role=WriterRole.DM, provider_id="scripted", model_id="scripted",
        )
        owner.append_agent_message(
            session_id=session.session_id, message_id="check", kind=SessionMessageKind.USER,
            content={"text": "I check the borrowed notebook."},
        )
        person = {**entity("tessa", kind), "known_by": ["tessa"]}
        hidden = {**entity("hidden-owner"), "visibility": "gm_private"}
        result = StatelessMcpMechanics(owner).commit_campaign_file(
            "public", session_id=session.session_id, basis_message_id="check", parameters={
                "state_change": "changed", "state_change_summary": "Record a public loan.",
                "entities": [person, hidden, entity("chest", "object")],
                "inventory_updates": [{
                    "expected_revision": owner.snapshot("public").revision,
                    "holdings": [holding(
                        holding_id="notebook", item_id="notebook", name="Notebook", quantity=1,
                        owner_id="hidden-owner", holder_id="tessa", known_by=["tessa"],
                    ), holding(
                        holding_id="secret", item_id="secret", name="Private item", quantity=1,
                        owner_id="tessa", holder_id="tessa", visibility="gm_private",
                    )],
                }],
            },
        )
        assert result["status"] == "campaign_file_updated"
        before = owner.snapshot("public")
        projected = owner.campaign_panels("public")["surfaces"]
        panel = next(p for p in projected if p["renderer"] == "generic_inventory")
        people = panel["data"]["characters"]
        assert [p["id"] for p in people] == ["tessa"]
        assert [item["name"] for item in people[0]["items"]] == ["Notebook"]
        assert people[0]["items"][0]["owner"] == "Unknown"
        assert "hidden-owner" not in str(panel) and "Private item" not in str(panel)
        assert owner.snapshot("public") == before
    with AuthorityOwner.open(database, "public-reopened") as owner:
        assert owner.campaign_panels("public")["surfaces"] == projected
        assert owner.snapshot("public") == before


@pytest.mark.parametrize("with_ruleset", [False, True])
def test_fallback_inventory_mcp_atomic_replay_and_reopen(tmp_path, monkeypatch, with_ruleset):
    database = tmp_path / "inventory.db"
    with AuthorityOwner.open(database, "inventory-owner") as owner:
        lock = PackageLock(entries=())
        if with_ruleset:
            # A panel named Inventory does not declare an inventory system.
            compiled = compile_layout(starting_layout())
            with materialize_package_archive((compiled.draft,)) as artifact:
                lock = owner.install_package_archive(
                    artifact.archive, install_id="rules"
                ).package_lock
        owner.create_campaign(
            CampaignCreate(
                campaign_id="inventory",
                display_name="Inventory",
                writer=WriterRole.DM,
                package_lock=lock,
                turn_id="play",
            )
        )
        session_id = owner.create_agent_session(
            campaign_id="inventory",
            role=WriterRole.DM,
            provider_id="scripted",
            model_id="scripted",
        ).session_id
        owner.append_agent_message(
            session_id=session_id,
            message_id="borrow",
            kind=SessionMessageKind.USER,
            content={"text": "May I borrow two arrows from the store?"},
        )
        mcp = StatelessMcpMechanics(owner)

        def commit(parameters):
            return mcp.commit_campaign_file(
                "inventory",
                session_id=session_id,
                basis_message_id="borrow",
                parameters=parameters,
            )

        def read():
            return json.loads(
                owner.read_campaign_reference(
                    "inventory",
                    source="campaign_state",
                    path="inventory",
                )["content"]
            )

        revision = owner.snapshot("inventory").revision
        establish = {
            "state_change": "changed",
            "state_change_summary": "Establish the store and arrows.",
            "entities": [entity("mira"), entity("waystation", "place"), entity("chest", "object")],
            "facts": [
                {
                    "fact_id": "sylvia-fallback-inventory",
                    "category": "continuity",
                    "text": "A note identity is separate from an entity identity.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                }
            ],
            "inventory_updates": [{"expected_revision": revision, "holdings": [holding()]}],
        }
        invalid = deepcopy(establish)
        invalid["entities"][0]["name"] = ""
        before = owner.snapshot("inventory")
        with pytest.raises(MechanicsRejected):
            commit(invalid)
        assert owner.snapshot("inventory") == before
        assert commit(establish)["status"] == "campaign_file_updated"
        seeded = owner.snapshot("inventory")
        initial_panel = owner.campaign_panels("inventory")["surfaces"]
        assert (
            len([panel for panel in initial_panel if panel["renderer"] == "generic_inventory"]) == 1
        )
        assert read()["holdings"][0]["quantity"] == 5
        assert commit(establish)["status"] == "campaign_file_updated"
        assert owner.snapshot("inventory") == seeded
        borrow = {
            "state_change": "changed",
            "state_change_summary": "Mira borrows two arrows.",
            "inventory_updates": [
                {
                    "expected_revision": seeded.revision,
                    "transfers": [
                        {
                            "source_id": "arrows-store",
                            "destination_id": "arrows-mira",
                            "quantity": 2,
                            "holder_id": "mira",
                        }
                    ],
                }
            ],
            "facts": [
                {
                    "fact_id": "loan",
                    "category": "continuity",
                    "text": "Return the arrows.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                }
            ],
        }
        stale = deepcopy(borrow)
        stale["inventory_updates"][0]["expected_revision"] = revision
        with pytest.raises(MechanicsRejected):
            commit(stale)
        assert owner.snapshot("inventory") == seeded
        append = sqlite_continuity.append_record

        def fail_note(*args, **kwargs):
            if kwargs.get("record_id") == "loan":
                raise OSError("Synthetic failure after the ledger write")
            return append(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(sqlite_continuity, "append_record", fail_note)
            with pytest.raises(MechanicsRejected) as error:
                commit(borrow)
            assert error.value.code == "campaign_file_save_failed"
        assert owner.snapshot("inventory") == seeded
        assert commit(borrow)["status"] == "campaign_file_updated"
        expected = owner.snapshot("inventory")
        assert [item["quantity"] for item in read()["holdings"]] == [3, 2]
        assert all(item["owner_id"] == "waystation" for item in read()["holdings"])
        panel = next(
            panel
            for panel in owner.campaign_panels("inventory")["surfaces"]
            if panel["renderer"] == "generic_inventory"
        )
        mira = next(item for item in panel["data"]["characters"] if item["id"] == "mira")
        assert mira["items"][0]["quantity"] == 2
        assert mira["items"][0]["owner"] == "Waystation"
        assert mira["items"][0]["holder"] == "Mira"
        assert "request_hash" not in str(panel)
        assert commit(borrow)["status"] == "campaign_file_updated"
        assert owner.snapshot("inventory") == expected
        assert (
            "Return the arrows"
            in owner.read_campaign_reference(
                "inventory",
                source="campaign_state",
                path="loan",
            )["content"]
        )
        # An explicit reused owner operation key must reject changed semantics as well.
        updates = [
            {
                "operation": "update_inventory",
                "expected_revision": expected.revision,
                "transfers": [
                    {
                        "source_id": "arrows-mira",
                        "destination_id": "arrows-store",
                        "quantity": 1,
                        "holder_id": "chest",
                    }
                ],
            }
        ]
        owner.update_campaign_file(
            session_id=session_id,
            update_id="return",
            updates=updates,
            basis_message_ids=("borrow",),
        )
        conflict = deepcopy(updates)
        conflict[0]["transfers"][0]["quantity"] = 2
        expected = owner.snapshot("inventory")
        with pytest.raises(IdempotencyConflict):
            owner.update_campaign_file(
                session_id=session_id,
                update_id="return",
                updates=conflict,
                basis_message_ids=("borrow",),
            )
        assert owner.snapshot("inventory") == expected
    with AuthorityOwner.open(database, "inventory-reopened") as owner:
        assert owner.snapshot("inventory") == expected
        inventory = json.loads(
            owner.read_campaign_reference(
                "inventory",
                source="campaign_state",
                path="inventory",
            )["content"]
        )
        assert [item["quantity"] for item in inventory["holdings"]] == [4, 1]
        panel = next(
            panel
            for panel in owner.campaign_panels("inventory")["surfaces"]
            if panel["renderer"] == "generic_inventory"
        )
        assert panel["data"]["characters"][0]["items"][0]["quantity"] == 1
