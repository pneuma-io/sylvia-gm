"""Public selection delivers current Lite while preserving the exact retained-content fences."""

import io
import json
import zipfile
from copy import deepcopy

import pytest
from tests.srd_fixture_scene import fixture_scene

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_campaign_plans import (
    bundled_plan,
    bundled_plans_root,
    read_plan_catalogue,
)
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.interview_content import resolve_interview_content
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.application.ready_campaigns import customize_ready_campaign, ready_campaign_catalog
from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected, RulesetPreconditionRejected, StaleRevision
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionMessageKind

NS = "sylvia.srd-lite.bg3"
MAP = "srd-coordinate-map"
PARTIES = ("rift-awakening", "last-cart-through", "the-cartographers-wake", "a-debt-in-iron")


def test_every_public_lite_plan_selects_current_exact_mechanics(tmp_path):
    with zipfile.ZipFile(io.BytesIO(bundled_srd_archive())) as source:
        primary = next(
            r for r in json.loads(source.read("manifest.json"))["packages"] if r["id"] == NS
        )
        runtime = source.read(f"packages/{NS}/runtime/ruleset.yaml")
        spells = source.read(f"packages/{NS}/content/spell-status-inventory.json")
    assert len(json.loads(spells)["spells"]) == 117
    # The bundled Plans must bind the Lite version this build ships, not an earlier one.
    rift = bundled_plan("rift-awakening").plan.payload["bindings"]["ruleset"]
    assert (rift["id"], rift["version"]) == (NS, primary["version"])
    surface = next(row for row in primary["ui_surfaces"] if row["id"] == MAP)
    assert "occupied_space" in surface["source"]["fields"]
    public = read_plan_catalogue(bundled_plans_root())
    assert len(public) == 17
    selected_closures = {}
    database = tmp_path / "selection.sqlite3"
    with AuthorityOwner.open(database, "public-selection") as owner:
        owner.set_admin_full_audit(True)
        for row in public:
            selected = bundled_plan(row["id"])
            if selected.plan.payload["bindings"]["ruleset"]["id"] != NS:
                assert row["id"] == "riichi-quick-test"
                continue
            with zipfile.ZipFile(io.BytesIO(selected.package_snapshot)) as archive:
                manifest_bytes = archive.read("manifest.json")
                manifest = json.loads(manifest_bytes)
                assert canonical_json_bytes(manifest) == manifest_bytes
                rule = next(r for r in manifest["packages"] if r["id"] == NS)
                assert rule == primary
                assert archive.read(f"packages/{NS}/runtime/ruleset.yaml") == runtime
                assert archive.read(f"packages/{NS}/content/spell-status-inventory.json") == spells
            plan_path = bundled_plans_root() / row["id"] / "plan.json"
            assert plan_path.read_bytes() == canonical_json_bytes(selected.plan.payload)
            content = resolve_interview_content(
                owner,
                tmp_path,
                {
                    "ruleset": {"candidate_id": NS},
                    "setting": {"setting_id": "pneuma.severed-lands"},
                    "plan": {"plan_id": row["id"]},
                },
            )
            final = resolve_campaign_library_selection(
                owner,
                tmp_path,
                candidate_id=NS,
                setting_id="pneuma.severed-lands",
                plan_id=row["id"],
            )
            assert final.candidate.package_lock == content.candidate.package_lock
            assert (
                content.candidate.closure_sha256
                == selected.plan.payload["bindings"]["package_closure_sha256"]
            )
            assert final.sources.ruleset.content_sha256 == primary["content_sha256"]
            selected_closures[row["id"]] = content.candidate.closure_sha256
        assert len(selected_closures) == 16
    with AuthorityOwner.open(database, "selection-reopen") as owner:
        owner.set_admin_full_audit(True)
        for plan_id, closure in selected_closures.items():
            final = resolve_campaign_library_selection(
                owner, tmp_path, candidate_id=NS, setting_id="pneuma.severed-lands", plan_id=plan_id
            )
            assert final.candidate.closure_sha256 == closure
    print(
        "All 16 ordinary public selections retain the exact current runtime and closure on reopen."
    )


@pytest.mark.parametrize(
    "field", ["ruleset", "campaign_setting", "package_closure_sha256", "required_state"]
)
def test_public_descendant_rejects_wrong_binding_or_missing_required_state(field):
    selected = bundled_plan("rift-awakening")
    payload = json.loads(canonical_json_bytes(selected.plan.payload))
    if field == "required_state":
        payload["initial_state"]["mechanics"] = []
        expected, match = ContentShapeError, "Content fields do not match"
    else:
        expected, match = ValueError, "bindings"
        if field == "package_closure_sha256":
            payload["bindings"][field] = "0" * 64
        else:
            payload["bindings"][field]["content_sha256"] = "0" * 64
    with pytest.raises(expected, match=match) as rejected:
        validate_plan_snapshot(selected.package_snapshot, payload, selected.plan.readable_markdown)
    if field == "required_state":
        assert any(
            d.code == "plan_starting_state_invalid"
            and d.path == "$.initial_state.mechanics"
            and "required starting key 'state'" in d.message
            for d in rejected.value.diagnostics
        )


def launch_public(owner, storage, plan_id, operation, *, scenario=None):
    """Complete real Interview and owner handoff using the selected prepared party.

    Movement cases add a declared synthetic map at initial admission, preserving every
    Plan-owned starting value. They use the public Plan, never a substitute package.
    """
    ready = next(row for row in ready_campaign_catalog() if row["id"] == plan_id)
    copied = customize_ready_campaign(
        owner,
        storage,
        operation_id="party-" + operation,
        template_id=plan_id,
        expected_content_sha256=ready["content_sha256"],
    )
    selection = resolve_campaign_library_selection(
        owner,
        storage,
        candidate_id=NS,
        setting_id="pneuma.severed-lands",
        plan_id=plan_id,
    )
    party_ids = [row["id"] for row in copied["characters"]]
    if selection.required_participants:
        party_ids[0] = selection.required_participants[0].artifact_id
    for row in copied["characters"]:
        assert row["ruleset"] == selection.sources.ruleset.as_payload()
        assert row["campaign_setting"] == selection.sources.setting.as_payload()
    session = "builder-" + operation
    draft = owner.create_guided_campaign_draft(
        operation_id=operation,
        display_name=operation,
        builder_session_id=session,
        builder_provider_id="unconfigured.local",
        builder_model_id="unconfigured.model",
    )
    answers = [
        ("ruleset", {"candidate_id": NS}),
        ("setting", {"setting_id": "pneuma.severed-lands"}),
        ("plan", {"plan_id": plan_id}),
        ("name", {"display_name": operation}),
        (
            "participants",
            {
                "archetype_id": party_ids[0],
                "companion_ids": party_ids[1:],
            },
        ),
        ("boundaries_preferences", {"boundaries": "", "preferences": ""}),
        ("specific_requests", {"no_additional_requests": True}),
    ]
    for step, answer in answers:
        assert draft.current_step == step
        draft = owner.advance_guided_campaign_draft(
            operation_id=operation + "-" + step,
            campaign_id=draft.campaign_id,
            builder_session_id=session,
            expected_construction_revision=draft.construction_revision,
            current_step=step,
            visible_answer="Synthetic " + step,
            answer=answer,
        )
    assert draft.current_step == "review"
    participants, initial, selected_party = materialize_library_party(
        owner,
        selection,
        operation_id=operation,
        player_character_id=party_ids[0],
        companion_ids=tuple(party_ids[1:]),
    )
    pc = participants[0].participant_id
    if scenario is not None:
        values = {(s.namespace, s.key): json.loads(canonical_json_bytes(s.value)) for s in initial}
        entities = values[(NS, "entities")]
        guard = deepcopy(entities[pc])
        guard.update(
            name="Synthetic guard",
            creature_kind="monster",
            size_rank=2,
            conditions=["incapacitated"] if scenario == "incapacitated" else [],
        )
        if scenario == "missing_rank":
            guard.pop("size_rank")
        entities["synthetic-guard"] = guard
        entities[pc]["initiative_modifier"] = 20
        entities[pc]["conditions"] = []
        guard["initiative_modifier"] = 0
        position = [50, 0, 0] if scenario in ("clear", "missing_rank") else [7.5, 0, 0]
        scene = fixture_scene(
            {pc: entities[pc], "synthetic-guard": guard},
            {pc: [0, 0, 0], "synthetic-guard": position},
        )
        for body in scene["entities"]:
            body["metadata"]["player"] = {"label": body["id"], "kind": "unit"}
        values[(NS, "state")]["spatial_scene"] = scene
        initial = tuple(
            StateValue(namespace, key, value) for (namespace, key), value in values.items()
        )
    owner.append_agent_message(
        session_id=session,
        message_id=operation + "-ready",
        kind=SessionMessageKind.USER,
        content={
            "text": "Ready to Begin",
            "interview_step": "ready",
            "interview_value": {"action": "ready_to_begin"},
        },
    )
    bound = owner.bind_guided_campaign_basis(
        operation_id=operation + "-bind",
        campaign_id=draft.campaign_id,
        display_name=operation,
        package_lock=selection.candidate.package_lock,
        turn_id=operation + "-construction",
        builder_session_id=session,
        package_candidate_id=selection.candidate.candidate_id,
        participants=participants,
        initial_state=initial,
    )
    owner.begin_campaign_construction(
        operation_id=operation + "-begin",
        campaign_id=draft.campaign_id,
        lane="quick_start",
        expected_revision=bound.resulting_revision,
        canonical_input={
            "plan_id": plan_id,
            "plan_content_sha256": selection.plan.content_sha256,
            "selected_party": selected_party,
        },
    )
    owner.finalize_campaign_construction(
        campaign_id=draft.campaign_id,
        dm_provider_id="unconfigured.local",
        dm_model_id="unconfigured.model",
        model_guidance_override=True,
        plan_artifact_text=selection.plan.plan.readable_markdown,
        structured_plan=selection.plan.plan.payload,
    )
    assert owner.snapshot(draft.campaign_id).writer is WriterRole.DM
    assert (
        owner.current_package_lock(draft.campaign_id).closure_sha256
        == selection.candidate.closure_sha256
    )
    return draft.campaign_id, pc


def action(owner, campaign, tool, parameters, identity):
    return ActionCommand(
        campaign_id=campaign,
        action_id=identity,
        idempotency_key=identity,
        owner_id="descendant-launch",
        writer=WriterRole.DM,
        expected_revision=owner.snapshot(campaign).revision,
        turn_id=owner.current_open_turn(campaign),
        package_id=NS,
        tool_name=tool,
        parameters=parameters,
    )


def test_public_interview_finalizes_all_prepared_party_routes_and_reopens(tmp_path):
    database = tmp_path / "launches.sqlite3"
    saved = []
    with AuthorityOwner.open(database, "descendant-launch") as owner:
        owner.set_admin_full_audit(True)
        for slug in PARTIES:
            campaign, _ = launch_public(owner, tmp_path, slug, slug)
            snapshot = owner.snapshot(campaign)
            assert all(
                p.package_sha256
                == bundled_plan(slug).plan.payload["bindings"]["ruleset"]["content_sha256"]
                for p in snapshot.participants
            )
            assert all(
                snapshot.state[NS + ".entities"][p.participant_id]["size_rank"]
                == p.data["size_rank"]
                for p in snapshot.participants
            )
            saved.append((campaign, snapshot, owner.current_package_lock(campaign)))
    with AuthorityOwner.open(database, "launch-reopen") as owner:
        owner.set_admin_full_audit(True)
        for campaign, snapshot, lock in saved:
            assert owner.snapshot(campaign) == snapshot
            assert owner.current_package_lock(campaign) == lock
    print(
        "Four public Interview paths finalized and reopened: "
        "Bard 1, Fighter 2, Rogue 2, fixed Tavin 1."
    )


@pytest.mark.parametrize(
    "scenario,cost",
    [("clear", 10), ("occupied", None), ("missing_rank", None), ("incapacitated", 25)],
)
def test_launched_public_descendant_map_uses_current_rules_and_durable_receipts(
    tmp_path, scenario, cost
):
    database = tmp_path / "movement.sqlite3"
    with AuthorityOwner.open(database, "descendant-launch") as owner:
        owner.set_admin_full_audit(True)
        campaign, pc = launch_public(owner, tmp_path, "rift-awakening", scenario, scenario=scenario)
        start = action(
            owner,
            campaign,
            "start_combat_encounter_entities",
            {
                "first_actor": pc,
                "second_actor": "synthetic-guard",
                "first_surprised": False,
                "second_surprised": False,
                "tie_winner": "first",
            },
            "start",
        )
        assert owner.execute_action(start).status is ActionStatus.COMMITTED
        begin = action(owner, campaign, "begin_combat_turn", {}, "begin")
        assert owner.execute_action(begin).status is ActionStatus.COMMITTED
        before = owner.snapshot(campaign)
        bodies = before.state[NS + ".state"]["spatial_scene"]["entities"]
        assert all(b["footprint"] == {"kind": "box", "half_extents": [0.4, 0.4]} for b in bodies)
        destination = (
            [4, 0, 0]
            if scenario == "occupied"
            else ([15, 0, 0] if scenario == "incapacitated" else [0, 10, 0])
        )
        panels = owner.campaign_panels(campaign)
        surface = next(row for row in panels["surfaces"] if row["id"] == MAP)
        proposed = {
            "campaign_id": campaign,
            "surface_id": MAP,
            "panel_action_id": "move-walk",
            "operation_id": "move",
            "expected_revision": before.revision,
            "selection": {
                "actor": pc,
                "aim_point": destination,
                "map_revision": surface["data"]["map_revision"],
            },
        }
        availability = owner.player_action_availability(
            campaign_id=campaign,
            expected_revision=before.revision,
            requests=[
                {
                    "source": {"kind": "panel", "surface_id": MAP, "action_id": "move-walk"},
                    "selection": proposed["selection"],
                }
            ],
        )
        if cost is None:
            assert availability["results"][0]["available"] is False
            with pytest.raises(CommandRejected):
                owner.preview_panel_action(**proposed)
            with pytest.raises(RulesetPreconditionRejected, match="cannot end|size_rank|rank"):
                owner.execute_action(
                    action(
                        owner,
                        campaign,
                        "move_srd_scene_creature",
                        {"actor_state": pc, "destination": destination},
                        "direct",
                    )
                )
            assert owner.panel_action_receipt(**proposed) is None
            assert owner.snapshot(campaign) == before
        else:
            assert availability["results"][0]["available"] is True
            preview = owner.preview_panel_action(**proposed)
            assert preview["map_preview"]["path"]["cost"] == pytest.approx(cost)
            assert owner.snapshot(campaign) == before
            with pytest.raises(CommandRejected, match="Preview and confirm"):
                owner.execute_panel_action(**proposed)
            receipt = owner.execute_panel_action(**proposed, confirmation=preview["confirmation"])
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.snapshot(campaign).state[NS + ".combat"][
                "movement_spent"
            ] == pytest.approx(cost)
            after = owner.snapshot(campaign)
            owner.create_campaign(
                CampaignCreate(
                    campaign_id="direct-control",
                    display_name="Synthetic command parity",
                    writer=WriterRole.DM,
                    package_lock=owner.current_package_lock(campaign).package_lock,
                    turn_id="direct-turn",
                    participants=before.participants,
                    initial_state=tuple(
                        StateValue(NS, key.removeprefix(NS + "."), value)
                        for key, value in before.state.items()
                    ),
                )
            )
            direct = action(
                owner,
                "direct-control",
                "move_srd_scene_creature",
                {"actor_state": pc, "destination": destination},
                "direct-move",
            )
            assert owner.execute_action(direct).status is ActionStatus.COMMITTED
            assert owner.snapshot("direct-control").state == after.state
            assert owner.execute_panel_action(**proposed) == receipt
            assert owner.snapshot(campaign) == after
            stale = {**proposed, "operation_id": "stale", "expected_revision": before.revision}
            with pytest.raises((CommandRejected, StaleRevision)):
                owner.preview_panel_action(**stale)
            assert owner.snapshot(campaign) == after
        saved, lock = owner.snapshot(campaign), owner.current_package_lock(campaign)
    with AuthorityOwner.open(database, "movement-reopen") as owner:
        owner.set_admin_full_audit(True)
        assert owner.snapshot(campaign) == saved
        assert owner.current_package_lock(campaign) == lock
        if cost is not None:
            assert owner.panel_action_receipt(**proposed) == receipt
            assert owner.execute_panel_action(**proposed) == receipt
            assert owner.snapshot(campaign) == saved
    print(f"Public Rift {scenario}: {cost if cost is not None else 'refused without mutation'}")
