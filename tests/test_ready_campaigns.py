"""Prepared parties cross real content, campaign admission and ordinary play contracts."""

import hashlib
import json
import os
import subprocess
from importlib.resources import files

import pytest

from sylvia_gm.application import campaign_library_selection, ready_campaigns
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.compatible_character_copy import authored_character_state
from sylvia_gm.application.reusable_participants import save_reusable_participant
from sylvia_gm.domain.authority import ActionStatus, PackageRole, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionMessageKind

EXPECTED = {
    "a-debt-in-iron": (("Tavin Bell", "fighter", 1), ("Oren Reed", "cleric", 1)),
    "last-cart-through": (("Bram Alder", "fighter", 2), ("Oren Reed", "cleric", 2)),
    "the-cartographers-wake": (
        ("Pip Venn", "rogue", 2),
        ("Bram Alder", "fighter", 2),
        ("Oren Reed", "cleric", 2),
    ),
    "rift-awakening": (
        ("Neri Vale", "bard", 1),
        ("Bram Alder", "fighter", 1),
        ("Oren Reed", "cleric", 1),
    ),
}


def command(template, identity="first"):
    row = next(row for row in ready_campaigns.ready_campaign_catalog() if row["id"] == template)
    return {
        "operation_id": identity,
        "template_id": template,
        "expected_content_sha256": row["content_sha256"],
        "display_name": row["title"],
        "provider_id": "scripted",
        "model_id": "scripted/model",
        "model_guidance_override": True,
    }


def test_four_parties_are_locally_playable_and_reopen_without_roster_construction(
    tmp_path, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Ready loading must not construct all starters or discover saved participants")

    monkeypatch.setattr(campaign_library_selection, "available_reusable_participants", forbidden)
    source = files("sylvia_gm.campaign").joinpath("ready-campaigns.json")
    original = source.read_bytes()
    database = tmp_path / "fresh.db"
    receipts = []
    with AuthorityOwner.open(database, "ready-tests") as owner:
        for template, expected in EXPECTED.items():
            result = ready_campaigns.start_ready_campaign(
                owner, tmp_path, **command(template, template)
            )
            assert result["local_ready"] is True
            snapshot = owner.snapshot(result["campaign_id"])
            assert snapshot.writer is WriterRole.DM
            assert owner.campaign_preparation_boundary(result["campaign_id"]) is None
            assert {
                (p.data["name"], p.data["classes"][0]["id"], p.data["level"])
                for p in snapshot.participants
            } == set(expected)
            entities = snapshot.state["sylvia.srd-lite.bg3.entities"]
            for participant in snapshot.participants:
                assert participant.data["party_role"] == (
                    "player_character"
                    if participant.data["name"] == expected[0][0]
                    else "companion"
                )
                actor = entities[participant.participant_id]
                assert actor["hp"] == actor["max_hp"]
                assert not actor["initial_advancement_queue"]
                assert actor["resources"]["hit_die"] == participant.data["level"]
                assert actor["inventory"]
                assert actor["weapon_sets"]["format"] == 1
                if participant.data["level"] == 2:
                    kind = participant.data["classes"][0]["id"]
                    assert actor["max_hp"] == {"fighter": 20, "cleric": 15, "rogue": 17}[kind]
                    if kind == "fighter":
                        assert actor["resources"]["action_surge"] == 1
                    if kind == "cleric":
                        assert actor["resources"]["channel_divinity"] == 2
                        assert actor["resources"]["spell_slot_1"] == 3
                        assert "shield_of_faith" in actor["prepared_spells"]
            session = owner.session_context(result["dm_session_id"])
            assert not any(message.kind is SessionMessageKind.TALK for message in session.messages)
            # An actual Ruleset action is admitted immediately, without any authoring/advancement.
            pc = next(
                p.participant_id
                for p in snapshot.participants
                if p.data["party_role"] == "player_character"
            )
            action = owner.execute_action(
                ActionCommand(
                    campaign_id=result["campaign_id"],
                    action_id=f"check-{template}",
                    idempotency_key=f"check-{template}",
                    turn_id=result["dm_turn_id"],
                    owner_id="ready-tests",
                    writer=WriterRole.DM,
                    expected_revision=snapshot.revision,
                    package_id="sylvia.srd-lite.bg3",
                    tool_name="ability_check",
                    parameters={
                        "actor": pc,
                        "actor_state": pc,
                        "ability": "wisdom",
                        "skill": "perception",
                        "target_number": 10,
                        "circumstantial_modifier": 0,
                    },
                )
            )
            assert action.status is ActionStatus.COMMITTED, action
            before_replay = owner.snapshot(result["campaign_id"])
            replay = ready_campaigns.start_ready_campaign(
                owner, tmp_path, **command(template, template)
            )
            assert replay["campaign_id"] == result["campaign_id"]
            assert owner.snapshot(result["campaign_id"]) == before_replay
            receipts.append((result, before_replay))
        assert len(owner.campaign_index()) == 4
    with AuthorityOwner.open(database, "reopened") as owner:
        for result, saved in receipts:
            assert owner.snapshot(result["campaign_id"]) == saved
            assert owner.campaign_preparation_boundary(result["campaign_id"]) is None
    assert source.read_bytes() == original
    assert not (tmp_path / "bundled-srd-starters").exists()
    (tmp_path / "ready-timings.json").write_text(
        json.dumps([r["timing_ms"] for r, _ in receipts], indent=2)
    )


def test_second_copy_and_customization_preserve_templates_and_progress(tmp_path):
    database = tmp_path / "copies.db"
    with AuthorityOwner.open(database, "copies") as owner:
        args = command("last-cart-through")
        first = ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
        second = ready_campaigns.start_ready_campaign(
            owner, tmp_path, **{**args, "operation_id": "second"}
        )
        before = owner.snapshot(second["campaign_id"])
        initial = owner.snapshot(first["campaign_id"])
        assert {p.participant_id for p in initial.participants}.isdisjoint(
            p.participant_id for p in before.participants
        )
        pc = initial.participants[0].participant_id
        owner.append_agent_message(
            session_id=first["dm_session_id"],
            message_id="token",
            kind=SessionMessageKind.USER,
            content={"text": "I keep the token."},
        )
        inventory = list(initial.state["sylvia.srd-lite.bg3.entities"][pc]["inventory"]) + [
            "wooden_token"
        ]
        owner.update_campaign_file(
            session_id=first["dm_session_id"],
            update_id="keep-token",
            basis_message_ids=("token",),
            updates=[
                {
                    "operation": "update_character",
                    "expected_revision": initial.revision,
                    "participant_id": pc,
                    "fields": {"inventory": inventory},
                }
            ],
        )
        saved = owner.snapshot(first["campaign_id"])
        assert owner.snapshot(second["campaign_id"]) == before
        copy_args = {key: args[key] for key in ("template_id", "expected_content_sha256")}
        copied = ready_campaigns.customize_ready_campaign(
            owner, tmp_path, operation_id="custom", **copy_args
        )
        assert len(copied["characters"]) == 2
        saved_party_files = {
            path.relative_to(tmp_path): path.read_bytes()
            for path in (tmp_path / "participants").rglob("*")
            if path.is_file()
        }
        assert (
            ready_campaigns.customize_ready_campaign(
                owner, tmp_path, operation_id="custom", **copy_args
            )
            == copied
        )
        assert {
            path.relative_to(tmp_path): path.read_bytes()
            for path in (tmp_path / "participants").rglob("*")
            if path.is_file()
        } == saved_party_files
        selected, party = ready_campaigns.resolve_ready_campaign(owner, tmp_path, **copy_args)
        primary = next(
            e for e in selected.candidate.package_lock.entries if e.role is PackageRole.PRIMARY
        )
        declaration = owner.exact_participant_declarations(
            primary.package_id, primary.version, primary.sha256
        )[0]
        revised = save_reusable_participant(
            tmp_path,
            operation_id="revised",
            role=party[0].role,
            summary=party[0].summary,
            authored_profile=party[0].authored_profile,
            participant={**party[0].participant, "name": "Bram Revised"},
            entity_state=authored_character_state(party[0].entity_state, declaration),
            package=primary,
            declaration=declaration,
            campaign_setting=selected.sources.setting,
            setting_data=party[0].setting_data,
        )
        assert revised.name == "Bram Revised" and revised.artifact_id != party[0].artifact_id
        assert (
            ready_campaigns.resolve_ready_campaign(owner, tmp_path, **copy_args)[1][0].name
            == "Bram Alder"
        )
        assert owner.snapshot(first["campaign_id"]) == saved
        assert owner.snapshot(second["campaign_id"]) == before


@pytest.mark.parametrize(
    "damage", ["stale_preview", "character_hash", "schema", "binding", "low_level"]
)
def test_invalid_ready_content_fails_before_campaign_creation(tmp_path, monkeypatch, damage):
    args = command("last-cart-through")
    entries = list(ready_campaigns._entries())
    row = next(r for r in entries if r["id"] == "last-cart-through")
    if damage == "stale_preview":
        args["expected_content_sha256"] = "0" * 64
    elif damage == "character_hash":
        row["party"][0]["content_sha256"] = "0" * 64
    elif damage == "schema":
        row["party"][0]["character"]["schema"]["content_sha256"] = "0" * 64
        row["party"][0]["content_sha256"] = hashlib.sha256(
            canonical_json_bytes(row["party"][0]["character"])
        ).hexdigest()
    elif damage == "binding":
        row["bindings"]["ruleset"]["content_sha256"] = "0" * 64
    else:
        # A valid lower-level card still requires preparation and must not pass as a ready party.
        source = next(r for r in entries if r["id"] == "rift-awakening")["party"][1]
        row["party"][0] = {**source, "role": "player_character"}
    monkeypatch.setattr(ready_campaigns, "_entries", lambda: tuple(entries))
    with AuthorityOwner.open(tmp_path / "invalid.db", "invalid") as owner:
        with pytest.raises(CommandRejected):
            ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
        assert owner.campaign_index() == ()


def test_interrupted_start_resumes_same_copy_and_preserves_real_selection(tmp_path, monkeypatch):
    database = tmp_path / "interrupted.db"
    args = command("a-debt-in-iron")
    with AuthorityOwner.open(database, "interrupted") as owner:
        original = owner.finalize_campaign_construction

        def interrupt(**_kwargs):
            raise OSError("Synthetic interruption before Plan admission")

        monkeypatch.setattr(owner, "finalize_campaign_construction", interrupt)
        with pytest.raises(OSError):
            ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
        assert len(owner.campaign_index()) == 1
        campaign_id = owner.campaign_index()[0].campaign_id
        assert owner.snapshot(campaign_id).writer is WriterRole.BUILDER
        monkeypatch.setattr(owner, "finalize_campaign_construction", original)
    with AuthorityOwner.open(database, "resume") as owner:
        result = ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
        assert result["campaign_id"] == campaign_id
        assert len(owner.campaign_index()) == 1
        basis = owner.campaign_construction_basis(campaign_id)
        messages = owner.session_context(basis.builder_session_id).messages
        user = [m for m in messages if m.kind is SessionMessageKind.USER]
        assert len(user) == 1 and user[0].content["ready_campaign_id"] == "a-debt-in-iron"
        assert not any(m.content.get("kind") == "campaign_interview_state" for m in messages)


def test_owner_transport_loads_ready_state_without_resolving_a_provider(tmp_path, monkeypatch):
    from threading import Event

    from sylvia_gm.interfaces import owner_process
    from sylvia_gm.provider._base import ProviderStreamChunk
    from tests.qa_attached_request import request_attached
    from tests.test_alpha_f6e_owner_session_transport import (
        _HarnessResolver,
        _OwnerHarness,
        _ScriptedProvider,
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("Local ready loading must not resolve a provider")

    resolve_provider = _HarnessResolver.__call__
    monkeypatch.setattr(_HarnessResolver, "__call__", forbidden)
    provider = _ScriptedProvider((ProviderStreamChunk(content="The driver waves you forward."),))
    harness = _OwnerHarness(tmp_path / "transport.db", provider)
    entered, release, expired = Event(), Event(), Event()
    start = owner_process.start_ready_campaign

    def delayed(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            expired.set()
            raise RuntimeError("The owner reader was blocked by local ready work")
        return start(*args, **kwargs)

    monkeypatch.setattr(owner_process, "start_ready_campaign", delayed)
    try:
        catalog = harness.request("ready-catalog", "ready_campaign_catalog")
        assert len(catalog) == 4
        harness.send(
            "ready-start",
            {"method": "start_ready_campaign", "params": command("last-cart-through")},
        )
        assert entered.wait(5)
        harness.request("index-during-ready", "campaign_index")
        assert not expired.is_set()
        release.set()
        reply = harness.read()
        assert reply["id"] == "ready-start" and reply["ok"] is True, reply
        result = reply["result"]
        assert result["local_ready"] is True
        (tmp_path / "owner-ready-timing.json").write_text(json.dumps(result["timing_ms"], indent=2))
        saved = harness.request(
            "ready-snapshot", "snapshot", {"campaign_id": result["campaign_id"]}
        )
        assert saved["writer"] == "dm"
        assert not provider.started.is_set()
        monkeypatch.setattr(_HarnessResolver, "__call__", resolve_provider)
        opening = request_attached(
            harness,
            "opening",
            "start_dm_session",
            {
                "campaign_id": result["campaign_id"],
                "session_id": result["dm_session_id"],
                "turn_id": result["dm_turn_id"],
            },
            campaign_id=result["campaign_id"],
            attempt_id=result["dm_attempt_id"],
        )
        assert opening["interruption"] is None, opening
        assert provider.started.is_set()
    finally:
        release.set()
        harness.close()


def test_interruption_after_handoff_replays_without_rewinding_the_campaign(tmp_path, monkeypatch):
    args = command("a-debt-in-iron")
    with AuthorityOwner.open(tmp_path / "after-handoff.db", "after-handoff") as owner:
        link = ready_campaigns.os.link

        def interrupted(*_args, **_kwargs):
            raise OSError("Synthetic interruption while making the convenience Plan copy")

        monkeypatch.setattr(ready_campaigns.os, "link", interrupted)
        with pytest.raises(OSError):
            ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
        campaign_id = owner.campaign_index()[0].campaign_id
        saved = owner.snapshot(campaign_id)
        assert saved.writer is WriterRole.DM
        monkeypatch.setattr(ready_campaigns.os, "link", link)
        result = ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
        assert result["campaign_id"] == campaign_id
        assert owner.snapshot(campaign_id) == saved
        assert len(owner.campaign_index()) == 1


@pytest.mark.parametrize("component", ["storage", "campaigns", "campaign-test"])
def test_plan_copies_reject_linked_ancestors_before_writing_outside_storage(tmp_path, component):
    storage = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == "storage":
        link = storage
    else:
        storage.mkdir()
        link = storage / "campaigns"
        if component == "campaign-test":
            link.mkdir()
            link /= component
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) not in {5, 1314}:
            raise
        # Both junction endpoints are isolated fixtures; no privilege changes are needed.
        made = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, text=True, check=False,
        )
        if made.returncode:
            pytest.skip("A synthetic directory link is unavailable")
    plan = ready_campaigns.bundled_plan("last-cart-through").plan
    with pytest.raises(CommandRejected) as rejected:
        ready_campaigns._copy_ready_plan(storage, "campaign-test", plan)
    assert rejected.value.code == "ready_campaign_copy_invalid"
    assert list(outside.iterdir()) == []


def test_plan_convenience_copies_preserve_user_edits(tmp_path):
    plan = ready_campaigns.bundled_plan("last-cart-through").plan
    ready_campaigns._copy_ready_plan(tmp_path, "campaign-test", plan)
    edited = tmp_path / "campaigns" / "campaign-test" / "campaign-plan.md"
    edited.write_text("My editable adventure notes", encoding="utf-8")
    ready_campaigns._copy_ready_plan(tmp_path, "campaign-test", plan)
    assert edited.read_text(encoding="utf-8") == "My editable adventure notes"


@pytest.mark.parametrize("damage", ["stale_hash", "missing_template"])
def test_terminal_template_rejection_proves_no_previous_creation(tmp_path, damage):
    args = command("last-cart-through")
    if damage == "stale_hash":
        args["expected_content_sha256"] = "0" * 64
    else:
        args["template_id"] = "removed-adventure"
    with AuthorityOwner.open(tmp_path / "not-created.db", "not-created") as owner:
        with pytest.raises(CommandRejected) as rejected:
            ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
        assert rejected.value.code == "ready_campaign_not_created"
        assert owner.campaign_index() == ()


def test_stale_retry_waits_for_inflight_creation_before_attesting_absence(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    args = command("last-cart-through")
    entered, release, retry_requested, retry_entered = Event(), Event(), Event(), Event()
    original = ready_campaigns._start_ready_campaign

    def gated(*positional, **keywords):
        if keywords["expected_content_sha256"] == args["expected_content_sha256"]:
            entered.set()
            assert release.wait(5)
        else:
            retry_entered.set()
        return original(*positional, **keywords)

    monkeypatch.setattr(ready_campaigns, "_start_ready_campaign", gated)
    with AuthorityOwner.open(tmp_path / "inflight.db", "inflight") as owner:
        def stale_retry():
            retry_requested.set()
            return ready_campaigns.start_ready_campaign(
                owner, tmp_path, **{**args, "expected_content_sha256": "0" * 64}
            )

        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(ready_campaigns.start_ready_campaign, owner, tmp_path, **args)
            try:
                assert entered.wait(5)
                retry = workers.submit(stale_retry)
                assert retry_requested.wait(5)
                assert not retry_entered.wait(0.1)
            finally:
                release.set()
            result = first.result(timeout=60)
            assert result["local_ready"] is True
            with pytest.raises(CommandRejected) as rejected:
                retry.result(timeout=10)
            assert rejected.value.code == "ready_campaign_invalid"
        assert retry_entered.is_set()
        assert len(owner.campaign_index()) == 1
        before = owner.snapshot(result["campaign_id"])
        # The valid lost-reply retry still returns the same playable copy, without rewinding.
        replay = ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
        assert replay["campaign_id"] == result["campaign_id"]
        assert owner.snapshot(result["campaign_id"]) == before


@pytest.mark.parametrize("failure", ["transient_read", "permanent_template"])
def test_retry_overtaking_an_accepted_start_before_its_lane_is_safe(tmp_path, monkeypatch, failure):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    args = command("last-cart-through")
    accepted, release = Event(), Event()
    entry = ready_campaigns._entry
    failed = False

    def one_failure(*positional, **keywords):
        nonlocal failed
        if not failed:
            failed = True
            if failure == "transient_read":
                raise OSError("Synthetic one-off prepared content read failure")
            raise ValueError("The selected template is absent from this inventory")
        return entry(*positional, **keywords)

    with AuthorityOwner.open(tmp_path / "before-lane.db", "before-lane") as owner:
        lane = owner.ready_campaign_start_work
        first = True

        @contextmanager
        def before_lane(*positional, **keywords):
            nonlocal first
            if first:
                first = False
                accepted.set()
                assert release.wait(5)
            with lane(*positional, **keywords):
                yield

        monkeypatch.setattr(owner, "ready_campaign_start_work", before_lane)
        monkeypatch.setattr(ready_campaigns, "_entry", one_failure)
        with ThreadPoolExecutor(max_workers=1) as workers:
            earlier = workers.submit(ready_campaigns.start_ready_campaign, owner, tmp_path, **args)
            try:
                assert accepted.wait(5)
                with pytest.raises(CommandRejected) as rejected:
                    ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
                assert rejected.value.code == (
                    "ready_campaign_unavailable" if failure == "transient_read"
                    else "ready_campaign_not_created"
                )
            finally:
                release.set()
            if failure == "permanent_template":
                with pytest.raises(CommandRejected) as late_error:
                    earlier.result(timeout=60)
                assert late_error.value.code == "ready_campaign_not_created"
                assert owner.campaign_index() == ()
                # A terminal operation never becomes live again after content recovers.
                with pytest.raises(CommandRejected) as replay_error:
                    ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
                assert replay_error.value.code == "ready_campaign_not_created"
                replacement = ready_campaigns.start_ready_campaign(
                    owner, tmp_path, **{**args, "operation_id": "replacement"}
                )
                assert replacement["local_ready"] is True
                confirmed = owner.snapshot(replacement["campaign_id"])
                suffix = hashlib.sha256(args["operation_id"].encode()).hexdigest()[:24]
                fenced_id = f"ready-confirm-{suffix}"
                with pytest.raises(CommandRejected) as direct:
                    owner.confirm_new_campaign(
                        operation_id=fenced_id, display_name="Late direct confirmation",
                        package_lock=confirmed.package_lock, turn_id="late-direct-turn",
                    )
                assert direct.value.code == "ready_campaign_not_created"
            else:
                late_result = earlier.result(timeout=60)
                assert late_result["local_ready"] is True
                replay_result = ready_campaigns.start_ready_campaign(owner, tmp_path, **args)
                assert replay_result["campaign_id"] == late_result["campaign_id"]
            assert len(owner.campaign_index()) == 1
