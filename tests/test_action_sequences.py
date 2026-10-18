"""Executable intentions use admitted native controls and preserve committed prefixes.

Lite and TD cases use their actual admitted archives. The host-choice case uses
the existing labelled synthetic control; this does not expose a new Lite player
control or claim a real Lite panel/card choice journey.
"""

import json
import io
import os
import threading
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.domain.authority import (
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
    thaw_json_value,
)
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import CANDIDATE_ID, prepare_library_database
from tests.lite_imported_fixture import ImportedCase
from tests.test_lite_panel_combat import MAP, NS
from tests.test_lite_panel_combat import case as lite_combat_case
from tests.test_lite_panel_combat import _native_lite_archive
from tests.test_player_actions import _create as create_synthetic_control

TD = "pneuma-io.trigger-discipline"
GM = "queue-gm"
# Split Euclidean segments accumulate IEEE-754 residue (observed 1.78e-15 ft).
# This tolerance is 5e11 times smaller than the native 0.5 ft minimum charge.
NATIVE_BUDGET_ABS_TOLERANCE_FT = 1e-12
ROOT = Path(__file__).resolve().parents[1]


def test_native_control_fixture_uses_current_bundled_lite_declarations():
    with zipfile.ZipFile(io.BytesIO(_native_lite_archive())) as archive:
        record = next(item for item in json.loads(archive.read("manifest.json"))["packages"]
                      if item["id"] == NS)
    current = json.loads((ROOT / "rulesets/srd-lite/package-record.json").read_text(encoding="utf-8"))
    assert record["version"] == current["version"]
    panel = next(item for item in record["ui_surfaces"] if item["id"] == MAP)
    assert {"cast-magic_missile", "attack-melee-strength", "dash"} <= {
        item["id"] for item in panel["actions"]
    }


class NativeCase:
    def __init__(self, path, campaign_id, owner_id):
        self.path = path
        self.campaign_id = campaign_id
        self.owner_id = owner_id
        self.manager = None
        self.open()

    def open(self):
        self.manager = AuthorityOwner.open(self.path, self.owner_id)
        self.owner = self.manager.__enter__()

    def close(self):
        if self.manager is not None:
            self.manager.__exit__(None, None, None)
            self.manager = None

    def snapshot(self):
        return self.owner.snapshot(self.campaign_id)

    def reopen(self):
        before = self.snapshot()
        self.close()
        self.open()
        assert self.snapshot() == before


def create_session(owner, campaign_id, session_id=GM):
    owner.create_agent_session(
        campaign_id=campaign_id,
        session_id=session_id,
        role=WriterRole.DM,
        provider_id="synthetic",
        model_id="synthetic",
    )


@pytest.fixture
def td(tmp_path):
    archive = Path(
        os.environ.get(
            "SYLVIA_TD_CANDIDATE",
            ROOT / "tests/fixtures/td-native-panels-0.1.6.sylvia-gm-package",
        )
    )
    with zipfile.ZipFile(archive) as package:
        record = next(
            row for row in json.loads(package.read("manifest.json"))["packages"] if row["id"] == TD
        )
        scenario = json.loads(package.read(f"packages/{TD}/conformance/panel-edge-combat.json"))
        character = json.loads(package.read(f"packages/{TD}/conformance/character.json"))
    database = tmp_path / "native-td.sqlite3"
    prepare_library_database(database, archive.read_bytes())
    value = NativeCase(database, "queue-td", "queue-owner")
    try:
        artifact = record["participant_artifacts"][0]
        candidate = value.owner.package_candidate(CANDIDATE_ID)
        value.owner.create_campaign(
            CampaignCreate(
                campaign_id=value.campaign_id,
                display_name="Synthetic native TD action sequence",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                participants=(
                    Participant(
                        participant_id="hex",
                        kind=artifact["kind"],
                        package_id=TD,
                        package_version=record["version"],
                        package_sha256=record["content_sha256"],
                        schema_id=artifact["schema_id"],
                        schema_sha256=artifact["schema_sha256"],
                        validation_status=ParticipantValidationStatus.VALIDATED,
                        data=character["artifact"],
                    ),
                ),
                initial_state=tuple(
                    StateValue(TD, key.removeprefix(TD + "."), data)
                    for key, data in scenario["initial_state"].items()
                ),
            )
        )
        create_session(value.owner, value.campaign_id)
        yield value
    finally:
        value.close()


@pytest.fixture
def lite(tmp_path):
    value = lite_combat_case(tmp_path / "native-lite.sqlite3", "magic_missile")
    try:
        value.campaign_id = "lite"
        create_session(value.owner, value.campaign_id)
        yield value
    finally:
        value.close()


def td_step(identity, action, actor="hex"):
    return {
        "step_id": identity,
        "source": {"kind": "panel", "surface_id": "operations.character", "action_id": action},
        "selection": None if actor is None else {"actor": actor},
    }


def lite_step(value, identity, action, *, actor="hero", target="guard", destination=None):
    panel = next(row for row in value.owner.campaign_panels("lite")["surfaces"] if row["id"] == MAP)
    declaration = next(row for row in panel["actions"] if row["id"] == action)
    selection = {"map_revision": panel["data"]["map_revision"]}
    for binding in (declaration.get("bindings") or {}).values():
        selection[binding] = (
            actor if binding == "actor" else target if binding == "target" else destination
        )
    return {
        "step_id": identity,
        "source": {"kind": "panel", "surface_id": MAP, "action_id": action},
        "selection": selection if declaration.get("bindings") else None,
    }


def compose(
    value, steps, *, sequence_id="native-sequence", expected_version=0, expected_revision=None
):
    return value.owner.compose_action_sequence(
        campaign_id=value.campaign_id,
        session_id=GM,
        sequence_id=sequence_id,
        expected_revision=value.snapshot().revision
        if expected_revision is None
        else expected_revision,
        expected_version=expected_version,
        steps=steps,
    )


def read_queue(value, sequence_id="native-sequence"):
    return value.owner.action_sequence(session_id=GM, sequence_id=sequence_id)


def execution_request(value, queue, *, confirm=True):
    return {
        "campaign_id": value.campaign_id,
        "session_id": GM,
        "sequence_id": queue["sequence_id"],
        "expected_version": queue["version"],
        **({"confirmation": queue.get("confirmation")} if confirm else {}),
    }


def execute(value, queue, *, confirm=True):
    return value.owner.execute_action_sequence(**execution_request(value, queue, confirm=confirm))


def assert_committed(queue, identities):
    committed = [row for row in queue["steps"] if row["status"] == "committed"]
    assert [row["step_id"] for row in committed] == identities, thaw_json_value(queue)
    assert all(row.get("receipt") is not None for row in committed)
    assert len({row["operation_id"] for row in committed}) == len(committed)
    return committed


def test_native_td_multi_actor_order_and_future_stance_are_preview_only(td):
    before = td.snapshot()
    steps = [
        td_step("hex-down", "stance-crouched"),
        td_step("vesper-down", "stance-crouched", "vesper"),
        td_step("hex-up", "stance-standing"),
    ]
    queue = compose(td, steps)
    assert td.snapshot() == before
    assert [row["step_id"] for row in queue["steps"]] == [row["step_id"] for row in steps]
    assert all(row["status"] == "pending" for row in queue["steps"])
    assert read_queue(td) == queue
    assert td.snapshot() == before

    result = execute(td, queue)
    rows = assert_committed(result, ["hex-down", "vesper-down", "hex-up"])
    after = td.snapshot()
    assert after.state[TD + ".entities"]["hex"]["ap"] == 4
    assert after.state[TD + ".entities"]["vesper"]["ap"] == 5
    assert after.state[TD + ".entities"]["hex"]["stance"] == "STANDING"
    assert after.state[TD + ".entities"]["vesper"]["stance"] == "CROUCHED"
    assert after.state[TD + ".state"]["decision_owner"] == "player"
    assert [row.action_id for row in after.actions] == [row["operation_id"] for row in rows]
    assert execute(td, queue) == result
    assert td.snapshot() == after
    td.reopen()
    assert read_queue(td) == result
    assert execute(td, queue) == result
    assert td.snapshot() == after


@pytest.mark.parametrize("actor", ["hostile_alpha", "fpv_alpha", "missing"])
def test_actual_td_denies_visible_enemy_ineligible_device_and_unknown_actor(td, actor):
    before = td.snapshot()
    with pytest.raises(CommandRejected):
        compose(td, [td_step("forbidden", "stance-crouched", actor)])
    assert td.snapshot() == before
    assert not td.snapshot().actions


def test_queue_editing_and_reading_change_no_native_mechanics(td):
    before = td.snapshot()
    first = compose(
        td,
        [
            td_step("one", "stance-crouched"),
            td_step("two", "stance-standing"),
            td_step("three", "stance-prone", "vesper"),
        ],
    )
    edited = compose(
        td,
        [td_step("three", "stance-prone", "vesper"), td_step("one", "stance-crouched")],
        expected_version=first["version"],
    )
    assert edited["version"] > first["version"]
    assert [row["step_id"] for row in edited["steps"]] == ["three", "one"]
    assert td.snapshot() == before
    assert read_queue(td) == edited
    td.reopen()
    assert read_queue(td) == edited
    assert td.snapshot() == before


def test_queue_version_and_confirmation_bind_exact_order(td):
    before = td.snapshot()
    original = compose(
        td, [td_step("one", "stance-crouched"), td_step("two", "stance-prone", "vesper")]
    )
    edited = compose(
        td,
        [td_step("two", "stance-prone", "vesper"), td_step("one", "stance-crouched")],
        expected_version=original["version"],
    )
    with pytest.raises(StaleRevision):
        compose(td, [td_step("one", "stance-crouched")], expected_version=original["version"])
    with pytest.raises(StaleRevision):
        execute(td, original)
    with pytest.raises(CommandRejected):
        td.owner.execute_action_sequence(
            **{
                **execution_request(td, edited),
                "confirmation": original["confirmation"],
            }
        )
    assert td.snapshot() == before
    assert read_queue(td) == edited
    assert_committed(execute(td, edited), ["two", "one"])


@pytest.mark.parametrize("bad", ["duplicate", "extra-parameters", "raw-tool", "replacement-params"])
def test_queue_intentions_cannot_replace_declared_mechanics_or_duplicate_ids(td, bad):
    before = td.snapshot()
    steps = [td_step("first", "stance-crouched")]
    if bad == "duplicate":
        steps.append(td_step("first", "stance-prone", "vesper"))
    elif bad == "extra-parameters":
        steps[0]["parameters"] = {"actor": "hostile_alpha"}
    elif bad == "raw-tool":
        steps[0]["source"] = {"kind": "tool", "tool_name": "change_stance", "package_id": TD}
    else:
        steps[0]["source"]["params"] = {"actor": "hostile_alpha", "stance": "CROUCHED"}
    with pytest.raises(CommandRejected):
        compose(td, steps)
    assert td.snapshot() == before
    assert read_queue(td) is None


def test_sequence_identity_is_session_owned(td):
    queued = compose(td, [td_step("first", "stance-crouched")])
    create_session(td.owner, td.campaign_id, "other-session")
    before = td.snapshot()
    assert (
        td.owner.action_sequence(session_id="other-session", sequence_id=queued["sequence_id"])
        is None
    )
    with pytest.raises(CommandRejected):
        td.owner.execute_action_sequence(
            **{
                **execution_request(td, queued),
                "session_id": "other-session",
            }
        )
    assert read_queue(td) == queued
    assert td.snapshot() == before


def test_concurrent_duplicate_submission_cannot_spend_native_ap_twice(td, monkeypatch):
    queue = compose(td, [td_step("first", "stance-crouched"), td_step("second", "stance-standing")])
    entered, release = threading.Event(), threading.Event()
    real_execute = td.owner._execute_action
    held = False

    def hold_first_transaction(*args, **kwargs):
        nonlocal held
        if not held:
            held = True
            entered.set()
            assert release.wait(10), "Test must release the owned native transaction"
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(td.owner, "_execute_action", hold_first_transaction)
    results, failures = [], []

    def first_submit():
        try:
            results.append(execute(td, queue))
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=first_submit)
    worker.start()
    try:
        assert entered.wait(10), "Native transaction must reach the held seam"
        with pytest.raises(CommandRejected) as rejected:
            execute(td, queue)
        assert rejected.value.code in {"campaign_work_in_progress", "direct_action_in_progress"}
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert not failures
    assert len(results) == 1
    assert_committed(results[0], ["first", "second"])
    saved = td.snapshot()
    assert len(saved.actions) == 2
    assert saved.state[TD + ".entities"]["hex"]["ap"] == 4
    assert execute(td, queue) == results[0]
    assert td.snapshot() == saved


def test_native_td_resource_depletion_keeps_prefix_and_unspent_suffix(td):
    actions = [
        "stance-crouched",
        "stance-standing",
        "stance-prone",
        "stance-crouched",
        "stance-standing",
        "stance-prone",
        "stance-crouched",
    ]
    steps = [td_step(f"stance-{index}", action) for index, action in enumerate(actions)]
    before = td.snapshot()
    queue = compose(td, steps)
    assert td.snapshot() == before
    result = execute(td, queue)
    assert_committed(result, [f"stance-{index}" for index in range(6)])
    assert result["steps"][6]["status"] != "committed"
    assert result["steps"][6].get("error")
    after = td.snapshot()
    assert after.state[TD + ".entities"]["hex"]["ap"] == 0
    assert after.state[TD + ".entities"]["hex"]["stance"] == "PRONE"
    assert len(after.actions) == 6
    td.reopen()
    assert read_queue(td) == result
    assert td.snapshot() == after

    # Removing a stopped, uncommitted intention must preserve every committed row.
    shortened = compose(td, steps[:6], expected_version=result["version"])
    assert_committed(shortened, [f"stance-{index}" for index in range(6)])
    assert td.snapshot() == after
    with pytest.raises(CommandRejected):
        compose(td, steps[1:6], expected_version=shortened["version"])
    assert td.snapshot() == after


def test_native_td_confirmation_and_explicit_team_boundary(td):
    before = td.snapshot()
    queue = compose(
        td,
        [
            td_step("first", "stance-crouched"),
            td_step("end-team", "end-turn", None),
            td_step("later", "stance-crouched", "vesper"),
        ],
    )
    with pytest.raises(CommandRejected):
        execute(td, queue, confirm=False)
    assert td.snapshot() == before
    result = execute(td, read_queue(td))
    assert_committed(result, ["first", "end-team"])
    assert result["steps"][2]["status"] == "pending"
    after = td.snapshot()
    assert after.state[TD + ".state"]["active_side"] == "hostiles"
    assert after.state[TD + ".state"]["decision_owner"] == "gm"
    assert len(after.actions) == 2
    assert after.state[TD + ".entities"]["vesper"]["stance"] == "STANDING"
    td.reopen()
    assert read_queue(td) == result
    assert td.snapshot() == after


def test_external_native_change_stops_stale_queue_without_rebase(td):
    queue = compose(td, [td_step("intended", "stance-crouched")])
    current = td.snapshot()
    td.owner.execute_panel_action(
        campaign_id=td.campaign_id,
        session_id=GM,
        surface_id="operations.character",
        panel_action_id="stance-prone",
        operation_id="external-action",
        expected_revision=current.revision,
        selection={"actor": "vesper"},
    )
    changed = td.snapshot()
    try:
        stopped = execute(td, queue)
    except (StaleRevision, CommandRejected):
        stopped = read_queue(td)
    assert not [row for row in stopped["steps"] if row["status"] == "committed"]
    assert td.snapshot() == changed
    assert td.snapshot().state[TD + ".entities"]["hex"]["stance"] == "STANDING"


def test_agent_ownership_rejects_queue_execution_while_work_is_busy(td):
    queue = compose(td, [td_step("first", "stance-crouched")])
    before = td.snapshot()
    with td.owner.agent_session_work(GM), pytest.raises(CommandRejected) as rejected:
        execute(td, queue)
    assert rejected.value.code == "campaign_work_in_progress"
    assert td.snapshot() == before
    assert_committed(execute(td, read_queue(td)), ["first"])


def test_native_lite_move_cast_move_rechecks_own_map_revisions(lite):
    before = lite.snapshot()
    slots = before.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"]
    movement = before.state[NS + ".combat"]["movement_remaining"]
    queue = compose(
        lite,
        [
            lite_step(lite, "move-before", "move-walk", destination=[0, 5, 0]),
            lite_step(lite, "cast", "cast-magic_missile"),
            lite_step(lite, "move-after", "move-walk", destination=[0, 10, 0]),
        ],
    )
    assert lite.snapshot() == before
    result = execute(lite, queue)
    rows = assert_committed(result, ["move-before", "cast", "move-after"])
    after = lite.snapshot()
    assert after.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"] == slots - 1
    assert after.state[NS + ".combat"]["movement_remaining"] == movement - 10
    assert after.state[NS + ".combat"]["action_available"] is False
    assert after.state[NS + ".combat"]["turn_active"] is True
    assert [a.action_id for a in after.actions[len(before.actions) :]] == [
        r["operation_id"] for r in rows
    ]
    assert execute(lite, queue) == result
    assert lite.snapshot() == after
    lite.reopen()
    assert read_queue(lite) == result
    assert execute(lite, queue) == result
    assert lite.snapshot() == after


def test_native_lite_spent_action_stops_second_cast_without_a_second_slot(lite):
    before = lite.snapshot()
    slots = before.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"]
    queue = compose(
        lite,
        [
            lite_step(lite, "cast-once", "cast-magic_missile"),
            lite_step(lite, "cast-again", "cast-magic_missile"),
            lite_step(lite, "later", "move-walk", destination=[0, 5, 0]),
        ],
    )
    result = execute(lite, queue)
    assert_committed(result, ["cast-once"])
    assert result["steps"][1]["status"] != "committed"
    assert result["steps"][1].get("error")
    assert result["steps"][2]["status"] == "pending"
    after = lite.snapshot()
    assert after.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"] == slots - 1
    assert len(after.actions) == len(before.actions) + 1
    assert after.state[NS + ".combat"]["turn_active"] is True


def test_native_lite_displayed_npc_cannot_be_queued(lite):
    before = lite.snapshot()
    with pytest.raises(CommandRejected):
        compose(lite, [lite_step(lite, "npc", "move-walk", actor="guard", destination=[10, 0, 0])])
    assert lite.snapshot() == before


def test_actual_lite_companion_role_does_not_grant_player_control(tmp_path):
    value = ImportedCase(
        tmp_path / "lite-companion.sqlite3",
        "magic_missile",
        archive_bytes=_native_lite_archive(),
        participant_changes={"party_role": "companion"},
        scene_metadata_changes={
            identity: {"player": {"label": identity.title(), "kind": "unit"}}
            for identity in ("hero", "guard")
        },
    )
    try:
        value.campaign_id = "lite"
        value.act(
            "start_combat_encounter_entities",
            first_actor="hero",
            second_actor="guard",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        value.act("begin_combat_turn")
        create_session(value.owner, value.campaign_id)
        before = value.snapshot()
        assert before.participants[0].data["party_role"] == "companion"
        assert before.state[NS + ".combat"]["active_actor"] == "hero"
        # A native active turn and a displayed entity do not grant a companion control.
        with pytest.raises(CommandRejected, match="Ruleset currently requires a GM decision"):
            compose(value, [lite_step(value, "companion", "move-walk", destination=[0, 5, 0])])
        assert value.snapshot() == before
    finally:
        value.close()


def test_uncertain_post_commit_reply_recovers_native_receipt_without_replay(td, monkeypatch):
    queue = compose(td, [td_step("first", "stance-crouched"), td_step("later", "stance-standing")])
    real_execute = td.owner._execute_action
    lost = False

    def lose_after_native_commit(*args, **kwargs):
        nonlocal lost
        receipt = real_execute(*args, **kwargs)
        if not lost and receipt.status.value == "committed":
            lost = True
            raise ConnectionError("synthetic lost return after native transaction")
        return receipt

    monkeypatch.setattr(td.owner, "_execute_action", lose_after_native_commit)
    with pytest.raises(ConnectionError, match="lost return"):
        execute(td, queue)
    assert lost
    saved = td.snapshot()
    assert len(saved.actions) == 1
    assert saved.state[TD + ".entities"]["hex"]["ap"] == 5
    td.reopen()
    recovered = read_queue(td)
    committed = assert_committed(recovered, ["first"])
    assert recovered["status"] == "paused"
    assert committed[0]["receipt"]["action_id"] == saved.actions[0].action_id
    assert committed[0]["receipt"]["resulting_revision"] == saved.revision
    assert recovered["steps"][1]["status"] == "pending"
    assert read_queue(td) == recovered
    assert execute(td, queue) == recovered, "An old submission only reads the recovered stop"
    assert td.snapshot() == saved
    td.reopen()
    assert read_queue(td) == recovered
    assert td.snapshot() == saved

    # Resume only after an explicit revision/version-bound composition.
    resumed = compose(
        td,
        [td_step("first", "stance-crouched"), td_step("later", "stance-standing")],
        expected_version=recovered["version"],
    )
    assert td.snapshot() == saved
    final = execute(td, resumed)
    assert_committed(final, ["first", "later"])
    assert len(td.snapshot().actions) == 2
    assert td.snapshot().state[TD + ".entities"]["hex"]["ap"] == 4


def test_synthetic_declared_choice_pauses_and_reopens_without_new_lite_control(tmp_path):
    value = NativeCase(tmp_path / "synthetic-choice.sqlite3", "campaign", "owner")
    try:
        create_synthetic_control(value.owner, choice=True)
        create_session(value.owner, value.campaign_id)
        first = {
            "step_id": "choice-step",
            "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "move"},
            "selection": {"actor": "operator", "destination": [4, 4], "map_revision": 7},
        }
        later = {
            **first,
            "step_id": "later-step",
            "selection": {**first["selection"], "destination": [3, 3]},
        }
        before = value.snapshot()
        queue = compose(value, [first, later])
        waiting = execute(value, queue)
        assert waiting["steps"][0]["status"] == "awaiting_choice"
        assert waiting["steps"][1]["status"] == "pending"
        assert value.snapshot() == before
        operation_id = waiting["steps"][0]["operation_id"]
        value.reopen()
        assert read_queue(value) == waiting
        assert value.snapshot() == before
        receipt = value.owner.resolve_mechanics_choice(
            ResolveMechanicsChoiceCommand(
                campaign_id="campaign",
                action_id=operation_id,
                resolution_id="answer-route",
                owner_id="owner",
                expected_revision=before.revision,
                choice_id="route",
                option_id="north",
                session_id=GM,
            )
        )
        assert receipt.status.value == "committed"
        saved = value.snapshot()
        reconciled = read_queue(value)
        assert_committed(reconciled, ["choice-step"])
        assert reconciled["steps"][1]["status"] == "pending"
        assert value.snapshot() == saved
        shortened = compose(value, [first], expected_version=reconciled["version"])
        assert_committed(shortened, ["choice-step"])
        assert value.snapshot() == saved
        value.reopen()
        assert read_queue(value) == shortened
        assert value.snapshot() == saved
    finally:
        value.close()


@pytest.mark.parametrize("command", ["compose", "execute"])
def test_public_sequence_commands_reject_agent_work_bypass_flags(td, command):
    queue = compose(td, [td_step("first", "stance-crouched")])
    before = td.snapshot()
    with td.owner.agent_session_work(GM), pytest.raises(TypeError, match="from_agent"):
        if command == "compose":
            td.owner.compose_action_sequence(
                campaign_id=td.campaign_id,
                session_id=GM,
                sequence_id="bypass",
                expected_revision=before.revision,
                steps=[td_step("forbidden", "stance-prone")],
                from_agent=True,
            )
        else:
            td.owner.execute_action_sequence(**execution_request(td, queue), from_agent=True)
    assert read_queue(td) == queue
    assert td.snapshot() == before


def test_uncertain_commit_followed_by_foreign_change_is_stale_on_read_and_reopen(td, monkeypatch):
    queue = compose(td, [td_step("first", "stance-crouched"), td_step("later", "stance-standing")])
    real_execute = td.owner._execute_action
    lost = False

    def lose_after_native_commit(*args, **kwargs):
        nonlocal lost
        receipt = real_execute(*args, **kwargs)
        if not lost and receipt.status.value == "committed":
            lost = True
            raise ConnectionError("synthetic lost return after native transaction")
        return receipt

    monkeypatch.setattr(td.owner, "_execute_action", lose_after_native_commit)
    with pytest.raises(ConnectionError, match="lost return"):
        execute(td, queue)
    assert lost
    uncertain = td.snapshot()
    assert len(uncertain.actions) == 1
    td.reopen()
    td.owner.execute_panel_action(
        campaign_id=td.campaign_id,
        session_id=GM,
        surface_id="operations.character",
        panel_action_id="stance-prone",
        operation_id="foreign-after-uncertain",
        expected_revision=uncertain.revision,
        selection={"actor": "vesper"},
    )
    foreign = td.snapshot()
    assert len(foreign.actions) == 2
    td.reopen()
    recovered = read_queue(td)
    rows = assert_committed(recovered, ["first"])
    assert rows[0]["receipt"]["resulting_revision"] == uncertain.revision
    assert recovered["status"] == "stale"
    assert recovered.get("error")
    assert recovered["steps"][1]["status"] == "pending"
    assert read_queue(td) == recovered
    assert execute(td, queue) == recovered
    assert td.snapshot() == foreign
    assert foreign.state[TD + ".entities"]["hex"]["ap"] == 5
    td.reopen()
    assert read_queue(td) == recovered
    assert td.snapshot() == foreign


@pytest.mark.parametrize(
    "target,destination,legal",
    [
        ([30, 0, 0], [25, 0, 0], True),
        ([5, 0, 0], [0, 25, 0], False),
    ],
)
def test_native_lite_melee_reach_is_recomputed_after_committed_movement(
    tmp_path, target, destination, legal
):
    value = lite_combat_case(tmp_path / "changed-reach.sqlite3", target=target)
    value.campaign_id = "lite"
    try:
        create_session(value.owner, value.campaign_id)
        before = value.snapshot()
        queue = compose(
            value,
            [
                lite_step(value, "move", "move-walk", destination=destination),
                lite_step(value, "attack", "attack-melee-strength"),
            ],
        )
        assert value.snapshot() == before
        result = execute(value, queue)
        assert_committed(result, ["move", "attack"] if legal else ["move"])
        after = value.snapshot()
        combat = after.state[NS + ".combat"]
        assert (
            combat["movement_remaining"] == before.state[NS + ".combat"]["movement_remaining"] - 25
        )
        assert combat["turn_active"] is True
        if not legal:
            assert result["steps"][1].get("error")
            assert combat["action_available"] is True
            assert (
                after.state[NS + ".entities"]["guard"]["hp"]
                == before.state[NS + ".entities"]["guard"]["hp"]
            )
        assert len(after.actions) == len(before.actions) + (2 if legal else 1)
        value.reopen()
        assert read_queue(value) == result
        assert value.snapshot() == after
    finally:
        value.close()


def test_native_lite_next_destination_uses_real_geometry_after_first_move(tmp_path):
    obstacle = {
        "id": "blocked-destination",
        "map_id": "lite",
        "volume": {"kind": "box", "min": [-2, 8, 0], "max": [2, 12, 10]},
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"source_ref": "synthetic-crate"},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
    }
    value = ImportedCase(
        tmp_path / "blocked-destination.sqlite3",
        "magic_missile",
        archive_bytes=_native_lite_archive(),
        participant_changes={"party_role": "player_character"},
        positions={"hero": [0, 0, 0], "guard": [5, 0, 0]},
        scene_metadata_changes={
            identity: {"player": {"label": identity.title(), "kind": "unit"}}
            for identity in ("hero", "guard")
        },
        scene_geometry=[obstacle],
    )
    value.campaign_id = "lite"
    try:
        value.act(
            "start_combat_encounter_entities",
            first_actor="hero",
            second_actor="guard",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        value.act("begin_combat_turn")
        create_session(value.owner, value.campaign_id)
        before = value.snapshot()
        queue = compose(
            value,
            [
                lite_step(value, "first", "move-walk", destination=[0, 5, 0]),
                lite_step(value, "blocked", "move-walk", destination=[0, 10, 0]),
                lite_step(value, "later", "cast-magic_missile"),
            ],
        )
        assert value.snapshot() == before
        result = execute(value, queue)
        assert_committed(result, ["first"])
        assert result["steps"][1].get("error")
        assert result["steps"][2]["status"] == "pending"
        after = value.snapshot()
        scene = after.state[NS + ".state"]["spatial_scene"]
        assert next(row["position"] for row in scene["entities"] if row["id"] == "hero") == [
            0,
            5,
            0,
        ]
        assert (
            after.state[NS + ".combat"]["movement_remaining"]
            == before.state[NS + ".combat"]["movement_remaining"] - 5
        )
        assert (
            after.state[NS + ".entities"]["hero"]["resources"]
            == before.state[NS + ".entities"]["hero"]["resources"]
        )
        assert len(after.actions) == len(before.actions) + 1
        value.reopen()
        assert read_queue(value) == result
        assert value.snapshot() == after
    finally:
        value.close()


def test_real_lite_saved_reaction_boundary_stops_old_intentions_without_implicit_completion(lite):
    # Lite currently declares no player panel/card for begin_srd_spell_action.
    # A genuine public native stage tests boundary admission, not a new player control.
    before = lite.snapshot()
    queue = compose(lite, [lite_step(lite, "later", "move-walk", destination=[0, 5, 0])])
    begun, begin_command = lite.act("begin_srd_spell_action", **lite.parameters)
    pending = lite.snapshot()
    event_id = pending.state[NS + ".state"]["pending_srd_action_stack"][-1]
    assert (
        pending.state[NS + ".entities"]["guard"]["hp"]
        == before.state[NS + ".entities"]["guard"]["hp"]
    )
    assert (
        pending.state[NS + ".entities"]["hero"]["resources"]
        == before.state[NS + ".entities"]["hero"]["resources"]
    )
    assert pending.state[NS + ".combat"]["action_available"] is False
    lite.reopen()
    assert lite.snapshot() == pending
    assert lite.execute(begin_command) == begun
    assert lite.snapshot() == pending
    stopped = execute(lite, queue)
    assert stopped["status"] == "stale"
    assert stopped["steps"][0]["status"] == "pending"
    assert lite.snapshot() == pending
    assert read_queue(lite) == stopped
    assert lite.snapshot().state[NS + ".state"]["pending_srd_action_stack"] == [event_id]

    finished, finish_command = lite.act("complete_srd_pending_action", pending_action_id=event_id)
    after = lite.snapshot()
    assert (
        after.state[NS + ".entities"]["guard"]["hp"]
        < pending.state[NS + ".entities"]["guard"]["hp"]
    )
    assert (
        after.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"]
        == pending.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"] - 1
    )
    assert after.state[NS + ".state"]["pending_srd_action_stack"] == []
    assert len(after.actions) == len(before.actions) + 2
    lite.reopen()
    assert lite.snapshot() == after
    assert lite.execute(begin_command) == begun
    assert lite.execute(finish_command) == finished
    assert execute(lite, queue) == stopped
    assert lite.snapshot() == after
    fresh = compose(
        lite,
        [lite_step(lite, "later", "move-walk", destination=[0, 5, 0])],
        expected_version=stopped["version"],
    )
    final = execute(lite, fresh)
    assert_committed(final, ["later"])
    assert len(lite.snapshot().actions) == len(after.actions) + 1


def hostile_lite_case(
    path, *, guard_changes=None, second_hostile=False, scene_zones=(), guard_position=None
):
    value = ImportedCase(
        path,
        "magic_missile",
        archive_bytes=_native_lite_archive(),
        participant_changes={"party_role": "player_character"},
        positions={
            "hero": [0, 0, 0],
            "guard": guard_position or [5, 0, 0],
            **({"other": [-5, 0, 0]} if second_hostile else {}),
        },
        entity_changes={
            "hero": {
                "held_items": ["spear"],
                "inventory": ["spear"],
                "attitudes": {"guard": "hostile"},
            },
            "guard": {
                "attitudes": {"hero": "hostile"},
                "reaction_available": True,
                "available_reactions": ["opportunity_attack"],
                **(guard_changes or {}),
            },
            "other": {"attitudes": {"hero": "hostile" if second_hostile else "friendly"}},
        },
        scene_zones=scene_zones,
        scene_metadata_changes={
            "hero": {"enemy_of": ["guard"], "player": {"label": "Hero", "kind": "unit"}},
            "guard": {"enemy_of": ["hero"], "player": {"label": "Guard", "kind": "unit"}},
        },
    )
    value.campaign_id = "lite"
    value.act(
        "prepare_srd_weapon_set",
        actor_state="hero",
        loadout=[{"set_name": "melee", "main_hand": "spear", "off_hand": ""}],
    )
    value.act(
        "start_combat_encounter_entities",
        first_actor="hero",
        second_actor="guard",
        first_surprised=False,
        second_surprised=False,
        tie_winner="first",
    )
    value.act("begin_combat_turn")
    create_session(value.owner, value.campaign_id)
    return value


GUARD_SPEAR_ATTACK = {
    "reactor_state": "guard",
    "actor_can_see_target": True,
    "target_can_see_actor": True,
    "attack_distance": 5,
    "weapon": "spear",
    "attack_ability": "strength",
    "wielding_two_handed": False,
    "attack_modifier": 3,
    "damage_dice_count": 1,
    "damage_die_size": "6",
    "damage_modifier": 1,
    "damage_type": "piercing",
}


def qualify_actual_guard_trigger(value):
    before = value.snapshot()
    value.owner.create_campaign(
        CampaignCreate(
            campaign_id="guard-eligibility",
            display_name="Synthetic Guard native reaction eligibility",
            writer=WriterRole.DM,
            package_lock=before.package_lock,
            turn_id="turn",
            participants=before.participants,
            initial_state=tuple(
                StateValue(NS, key.removeprefix(NS + "."), data)
                for key, data in before.state.items()
            ),
        )
    )
    receipt = value.owner.execute_action(
        ActionCommand(
            campaign_id="guard-eligibility",
            action_id="qualify-spear-reaction",
            idempotency_key="qualify-spear-reaction",
            owner_id="lite-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn",
            package_id=NS,
            tool_name="trigger_opportunity_attack",
            parameters=GUARD_SPEAR_ATTACK,
        )
    )
    assert receipt.status.value == "committed", receipt.result
    qualified = value.owner.snapshot("guard-eligibility")
    assert qualified.state[NS + ".combat"]["opportunity_attack_pending"] is True
    assert qualified.state[NS + ".combat"]["pending_reaction"]["reacting_entity"] == "guard"
    assert qualified.state[NS + ".combat"]["pending_reaction"]["weapon"] == "spear"
    assert qualified.state[NS + ".entities"]["guard"]["reaction_available"] is True
    assert (
        qualified.state[NS + ".entities"]["hero"]["hp"]
        == before.state[NS + ".entities"]["hero"]["hp"]
    )
    assert value.snapshot() == before, "Qualification must not mutate the tested campaign"


def test_actual_lite_melee_then_leave_reach_pauses_at_saved_native_reaction(tmp_path):
    value = hostile_lite_case(tmp_path / "real-opportunity.sqlite3")
    try:
        qualify_actual_guard_trigger(value)
        before = value.snapshot()
        scene = thaw_json_value(before.state[NS + ".state"]["spatial_scene"])
        interval = SpatialEngine(scene).query(
            {
                "kind": "body_proximity",
                "map_id": "lite",
                "moving_entity_id": "hero",
                "fixed_entity_id": "guard",
                "destination": [0, 15, 0],
                "radius": 5,
            }
        )
        interval = interval.as_payload()
        assert interval["hit"] is True and interval["entry"] == 0 and interval["exit"] < 1
        queue = compose(
            value,
            [
                lite_step(value, "fight", "attack-melee-strength"),
                lite_step(value, "leave", "move-walk", destination=[0, 15, 0]),
                lite_step(value, "suffix", "move-walk", destination=[0, 20, 0]),
            ],
        )
        result = execute(value, queue)
        after = value.snapshot()
        assert after.state[NS + ".entities"]["hero"]["fighting_actor_ids"] == ["guard"]
        assert after.state[NS + ".entities"]["guard"]["fighting_actor_ids"] == ["hero"]
        assert after.state[NS + ".combat"]["opportunity_attack_pending"] is True
        assert result["status"] == "paused"
        assert result["pause_summary"] == "Resolve Movement Reaction decision before continuing queued actions."
        assert "prepare_srd" not in result["pause_summary"]
        assert_committed(result, ["fight", "leave"])
        assert result["steps"][2]["status"] == "pending"
        remaining = after.state[NS + ".state"]["pending_srd_movement"]
        assert remaining["actor"] == "hero" and remaining["destination"] == [0, 15, 0]
        position = next(
            row["position"]
            for row in after.state[NS + ".state"]["spatial_scene"]["entities"]
            if row["id"] == "hero"
        )
        assert position != [0, 15, 0], (
            "The first move must stop before departure, preserving its destination"
        )
        assert after.state[NS + ".entities"]["guard"]["reaction_available"] is True
        assert len(after.actions) == len(before.actions) + 2
        assert read_queue(value) == result
        assert execute(value, queue) == result
        assert value.snapshot() == after
        value.reopen()
        assert read_queue(value) == result
        assert value.snapshot() == after
    finally:
        value.close()


def hero_position(value):
    return next(
        row["position"]
        for row in value.snapshot().state[NS + ".state"]["spatial_scene"]["entities"]
        if row["id"] == "hero"
    )


@pytest.mark.parametrize("take_reaction", [False, True])
@pytest.mark.parametrize("continue_movement", [False, True])
def test_actual_lite_opportunity_decision_resolution_and_completion_are_durable(
    tmp_path, take_reaction, continue_movement
):
    value = hostile_lite_case(tmp_path / "opportunity-completion.sqlite3")
    try:
        before = value.snapshot()
        queue = compose(
            value,
            [
                lite_step(value, "fight", "attack-melee-strength"),
                lite_step(value, "leave", "move-walk", destination=[0, 15, 0]),
                lite_step(value, "suffix", "move-walk", destination=[0, 20, 0]),
            ],
        )
        paused = execute(value, queue)
        assert paused["status"] == "paused"
        boundary = value.snapshot()
        point = hero_position(value)
        assert point != [0, 15, 0]
        partial_distance = sum(float(part) ** 2 for part in point) ** 0.5
        assert boundary.state[NS + ".combat"]["movement_remaining"] == pytest.approx(
            before.state[NS + ".combat"]["movement_remaining"] - partial_distance,
            rel=0,
            abs=NATIVE_BUDGET_ABS_TOLERANCE_FT,
        )
        # Ordinary native controls cannot bypass a saved continuation.
        value.reject("complete_srd_coordinate_movement", {"continue_movement": True})
        value.reject("end_combat_turn", {})
        value.reject("move_srd_scene_creature", {"actor_state": "hero", "destination": [0, 20, 0]})
        assert value.snapshot() == boundary
        decision = dict(GUARD_SPEAR_ATTACK)
        decision.pop("attack_distance")
        decision["take_reaction"] = take_reaction
        prepared, prepare_command = value.act("prepare_srd_coordinate_opportunity", **decision)
        prepared_state = value.snapshot()
        assert hero_position(value) == point
        assert prepared_state.state[NS + ".entities"]["guard"]["reaction_available"] is True
        assert (
            prepared_state.state[NS + ".entities"]["hero"]["hp"]
            == boundary.state[NS + ".entities"]["hero"]["hp"]
        )
        value.reopen()
        assert value.execute(prepare_command) == prepared
        assert value.snapshot() == prepared_state
        if take_reaction:
            pending = prepared_state.state[NS + ".combat"]["pending_reaction"]
            assert pending["kind"] == "opportunity_attack" and pending["weapon"] == "spear"
            assert prepared_state.state[NS + ".state"]["pending_srd_movement"]["stage"] == "attack"
            resolved, resolve_command = value.act(
                "resolve_opportunity_attack",
                target_is_charmer=False,
                fear_source_in_sight=False,
                advantage=False,
                disadvantage=False,
            )
            resolved_state = value.snapshot()
            context = resolved.result["context"]
            hero_before = prepared_state.state[NS + ".entities"]["hero"]
            hero_after = resolved_state.state[NS + ".entities"]["hero"]
            assert hero_before["hp"] == hero_before["max_hp"] == 20
            if context["_attack_outcome"] in {"hit", "critical_hit"}:
                damage = context["_damage_roll"]
                count = 2 if context["_attack_outcome"] == "critical_hit" else 1
                assert damage["sides"] == 6
                assert damage["count"] == count
                assert len(damage["results"]) == count
                assert all(1 <= result <= 6 for result in damage["results"])
                assert damage["total"] == sum(damage["results"])
                assert context["_damage_applied"] == damage["total"] + 1
                assert hero_after["hp"] == 20 - context["_damage_applied"]
            else:
                assert context["_attack_outcome"] == "miss"
                assert hero_after["hp"] == 20
            assert hero_after["hp"] > 0
            assert "unconscious" not in hero_after["conditions"]
            assert resolved_state.state[NS + ".entities"]["guard"]["reaction_available"] is False
            assert resolved_state.state[NS + ".combat"]["opportunity_attack_pending"] is False
            assert resolved_state.state[NS + ".combat"]["action_available"] is False
            assert (
                resolved_state.state[NS + ".combat"]["movement_remaining"]
                == boundary.state[NS + ".combat"]["movement_remaining"]
            )
            assert resolved_state.state[NS + ".state"]["pending_srd_movement"]["stage"] == "resume"
            value.reopen()
            assert value.execute(resolve_command) == resolved
            assert value.snapshot() == resolved_state
        else:
            assert prepared_state.state[NS + ".combat"]["opportunity_attack_pending"] is False
            assert prepared_state.state[NS + ".state"]["pending_srd_movement"]["stage"] == "resume"
        finish, finish_command = value.act(
            "complete_srd_coordinate_movement", continue_movement=continue_movement
        )
        final = value.snapshot()
        assert hero_position(value) == ([0, 15, 0] if continue_movement else point)
        assert final.state[NS + ".state"]["pending_srd_movement"] is None
        assert final.state[NS + ".entities"]["guard"]["reaction_available"] is (not take_reaction)
        expected_distance = 15 if continue_movement else partial_distance
        assert final.state[NS + ".combat"]["movement_remaining"] == pytest.approx(
            before.state[NS + ".combat"]["movement_remaining"] - expected_distance,
            rel=0,
            abs=NATIVE_BUDGET_ABS_TOLERANCE_FT,
        )
        assert final.state[NS + ".combat"]["action_available"] is False
        assert read_queue(value)["steps"][2]["status"] == "pending"
        value.reopen()
        assert value.execute(prepare_command) == prepared
        assert value.execute(finish_command) == finish
        assert execute(value, queue)["steps"][2]["status"] == "pending"
        assert value.snapshot() == final
        committed_inputs = [
            {
                "step_id": row["step_id"],
                "source": thaw_json_value(row["source"]),
                "selection": thaw_json_value(row["selection"]),
            }
            for row in paused["steps"][:2]
        ]
        edited = compose(
            value,
            committed_inputs + [lite_step(value, "suffix", "move-walk", destination=[0, 20, 0])],
            expected_version=read_queue(value)["version"],
        )
        completed = execute(value, edited)
        assert completed["status"] == "completed"
        assert_committed(completed, ["fight", "leave", "suffix"])
        assert hero_position(value) == [0, 20, 0]
        assert value.snapshot().state[NS + ".combat"]["movement_remaining"] == pytest.approx(
            before.state[NS + ".combat"]["movement_remaining"] - 20,
            rel=0,
            abs=NATIVE_BUDGET_ABS_TOLERANCE_FT,
        )
    finally:
        value.close()


@pytest.mark.parametrize(
    "guard_changes",
    [
        {"reaction_available": False},
        {"available_reactions": []},
        {"opportunity_attacks_blocked": True},
        {"conditions": ["stunned"]},
        {"attitudes": {"hero": "friendly"}},
    ],
)
def test_actual_lite_ineligible_guard_does_not_interrupt_supported_movement(
    tmp_path, guard_changes
):
    value = hostile_lite_case(
        tmp_path / "ineligible-opportunity.sqlite3", guard_changes=guard_changes
    )
    try:
        before = value.snapshot()
        queue = compose(value, [lite_step(value, "leave", "move-walk", destination=[0, 15, 0])])
        result = execute(value, queue)
        assert result["status"] == "completed"
        assert_committed(result, ["leave"])
        after = value.snapshot()
        assert hero_position(value) == [0, 15, 0]
        assert after.state[NS + ".state"].get("pending_srd_movement") is None
        assert after.state[NS + ".combat"].get("opportunity_attack_pending") is not True
        assert (
            after.state[NS + ".combat"]["movement_remaining"]
            == before.state[NS + ".combat"]["movement_remaining"] - 15
        )
        assert (
            after.state[NS + ".entities"]["guard"]["reaction_available"]
            == before.state[NS + ".entities"]["guard"]["reaction_available"]
        )
        assert (
            after.state[NS + ".entities"]["hero"]["hp"]
            == before.state[NS + ".entities"]["hero"]["hp"]
        )
    finally:
        value.close()


@pytest.mark.parametrize("budget_case", ["base", "dash", "dash-and-difficult"])
def test_actual_lite_tied_reactors_do_not_repeat_partial_movement_or_cost(tmp_path, budget_case):
    difficult = budget_case == "dash-and-difficult"
    zones = (
        [{
            "id": "actual-difficult-ground",
            "map_id": "lite",
            "shape": {"kind": "box", "min": [-20, -20, -5], "max": [20, 100, 10]},
            "metadata": {"plane": "material", "difficult": True},
        }]
        if difficult else []
    )
    value = hostile_lite_case(
        tmp_path / "tied-opportunity.sqlite3", second_hostile=True, scene_zones=zones
    )
    try:
        if budget_case != "base":
            dash = compose(value, [lite_step(value, "dash", "dash")], sequence_id="prior-dash")
            assert_committed(execute(value, dash), ["dash"])
        before = value.snapshot()
        allowance = 30 if budget_case == "base" else 60
        assert before.state[NS + ".combat"]["movement_remaining"] == allowance
        assert before.state[NS + ".combat"]["movement_spent"] == 0
        queue = compose(value, [lite_step(value, "leave", "move-walk", destination=[0, 15, 0])])
        result = execute(value, queue)
        assert result["status"] == "paused"
        first = value.snapshot()
        pending = first.state[NS + ".state"]["pending_srd_movement"]
        assert {row["actor"] for row in pending["candidates"]} == {"guard", "other"}
        assert pending["movement_budget_total"] == allowance
        assert (
            first.state[NS + ".combat"]["movement_remaining"]
            + first.state[NS + ".combat"]["movement_spent"]
        ) == allowance
        point = hero_position(value)
        partial_distance = sum(float(part) ** 2 for part in point) ** 0.5
        assert first.state[NS + ".combat"]["movement_spent"] == pytest.approx(
            partial_distance * (2 if difficult else 1),
            rel=0, abs=NATIVE_BUDGET_ABS_TOLERANCE_FT,
        )
        decision = dict(GUARD_SPEAR_ATTACK)
        decision.pop("attack_distance")
        decision["take_reaction"] = False
        value.act("prepare_srd_coordinate_opportunity", **decision)
        value.act("complete_srd_coordinate_movement", continue_movement=True)
        second = value.snapshot()
        assert hero_position(value) == point
        for key in ("movement_remaining", "movement_spent"):
            assert second.state[NS + ".combat"][key] == first.state[NS + ".combat"][key]
        assert second.state[NS + ".state"]["pending_srd_movement"]["stage"] == "selection"
        assert [
            row["actor"]
            for row in second.state[NS + ".state"]["pending_srd_movement"]["candidates"]
        ] == ["other"]
        value.reopen()
        assert value.snapshot() == second
        decision["reactor_state"] = "other"
        prepared, prepare_command = value.act("prepare_srd_coordinate_opportunity", **decision)
        prepared_state = value.snapshot()
        assert hero_position(value) == point
        for key in ("movement_remaining", "movement_spent"):
            assert prepared_state.state[NS + ".combat"][key] == second.state[NS + ".combat"][key]
        finished, finish_command = value.act(
            "complete_srd_coordinate_movement", continue_movement=True
        )
        final = value.snapshot()
        assert hero_position(value) == [0, 15, 0]
        assert final.state[NS + ".state"]["pending_srd_movement"] is None
        spent = 30 if difficult else 15
        remainder = allowance - spent
        assert final.state[NS + ".combat"]["movement_spent"] == spent
        assert final.state[NS + ".combat"]["movement_remaining"] == remainder
        assert final.state[NS + ".entities"]["guard"]["reaction_available"] is True
        assert final.state[NS + ".entities"]["other"]["reaction_available"] is True
        value.reopen()
        assert value.snapshot() == final
        assert value.execute(finish_command) == finished
        assert value.snapshot() == final

        # Reject a real excess atomically before spending the exact legal remainder.
        legal_distance = remainder / (2 if difficult else 1)
        excessive = compose(
            value,
            [lite_step(
                value, "excess", "move-walk", destination=[0, 15 + legal_distance + 0.5, 0]
            )],
            sequence_id="actual-over-budget",
        )
        rejected = execute(value, excessive)
        assert rejected["status"] == "failed"
        assert "enough movement" in rejected["error"]
        assert not rejected["committed_action_ids"]
        assert rejected["steps"][0]["receipt"] is None
        assert value.snapshot() == final
        exact = compose(
            value,
            [lite_step(value, "exact", "move-walk", destination=[0, 15 + legal_distance, 0])],
            sequence_id="actual-exact-remainder",
        )
        assert value.snapshot() == final
        completed = execute(value, exact)
        rows = assert_committed(completed, ["exact"])
        depleted = value.snapshot()
        assert hero_position(value) == [0, 15 + legal_distance, 0]
        assert depleted.state[NS + ".combat"]["movement_remaining"] == 0
        assert depleted.state[NS + ".combat"]["movement_spent"] == allowance
        assert len(depleted.actions) == len(final.actions) + 1
        receipt = next(
            action for action in depleted.actions if action.action_id == rows[0]["operation_id"]
        )
        assert receipt.result["context"]["_scene_movement_cost"] == remainder
        assert receipt.result["context"]["_movement_remaining_after"] == 0
        value.reopen()
        assert execute(value, exact) == completed
        assert value.snapshot() == depleted
    finally:
        value.close()


class ClonedLiteCase(NativeCase):
    command = ImportedCase.command
    execute = ImportedCase.execute
    act = ImportedCase.act
    reject = ImportedCase.reject


def test_actual_lite_full_size_body_starting_on_reach_edge_pauses_without_zero_move_cost(tmp_path):
    donor = hostile_lite_case(tmp_path / "edge-donor.sqlite3")
    try:
        facts = donor.snapshot()
        state = {key: thaw_json_value(data) for key, data in facts.state.items()}
        for body in state[NS + ".state"]["spatial_scene"]["entities"]:
            body["footprint"] = {"kind": "box", "half_extents": [2.5, 2.5]}
            body["position"] = [0, 0, 0] if body["id"] == "hero" else [10, 0, 0]
        scene = state[NS + ".state"]["spatial_scene"]
        interval = (
            SpatialEngine(scene)
            .query(
                {
                    "kind": "body_proximity",
                    "map_id": "lite",
                    "moving_entity_id": "hero",
                    "fixed_entity_id": "guard",
                    "destination": [-15, 0, 0],
                    "radius": 5,
                }
            )
            .as_payload()
        )
        assert interval["hit"] is True and interval["entry"] == 0 and interval["exit"] == 0
        path = tmp_path / "edge-actual.sqlite3"
        prepare_library_database(path, _native_lite_archive())
        value = ClonedLiteCase(path, "lite", "lite-owner")
        try:
            value.owner.create_campaign(
                CampaignCreate(
                    campaign_id="lite",
                    display_name="Synthetic actual Lite full size body reach boundary",
                    writer=WriterRole.DM,
                    package_lock=facts.package_lock,
                    turn_id="turn",
                    participants=facts.participants,
                    initial_state=tuple(
                        StateValue(NS, key.removeprefix(NS + "."), data)
                        for key, data in state.items()
                    ),
                )
            )
            create_session(value.owner, "lite")
            qualify_actual_guard_trigger(value)
            before = value.snapshot()
            queue = compose(value, [lite_step(value, "edge", "move-walk", destination=[-15, 0, 0])])
            paused = execute(value, queue)
            assert paused["status"] == "paused"
            assert_committed(paused, ["edge"])
            boundary = value.snapshot()
            assert hero_position(value) == [0, 0, 0]
            assert (
                boundary.state[NS + ".combat"]["movement_remaining"]
                == before.state[NS + ".combat"]["movement_remaining"]
            )
            assert boundary.state[NS + ".state"]["pending_srd_movement"]["destination"] == [
                -15,
                0,
                0,
            ]
            value.reopen()
            assert read_queue(value) == paused
            assert value.snapshot() == boundary
            decision = dict(GUARD_SPEAR_ATTACK)
            decision.pop("attack_distance")
            decision["take_reaction"] = False
            value.act("prepare_srd_coordinate_opportunity", **decision)
            finish, command = value.act("complete_srd_coordinate_movement", continue_movement=True)
            final = value.snapshot()
            assert hero_position(value) == [-15, 0, 0]
            assert (
                final.state[NS + ".combat"]["movement_remaining"]
                == before.state[NS + ".combat"]["movement_remaining"] - 15
            )
            assert final.state[NS + ".entities"]["guard"]["reaction_available"] is True
            value.reopen()
            assert value.execute(command) == finish
            assert value.snapshot() == final
        finally:
            value.close()
    finally:
        donor.close()


@pytest.mark.parametrize("distance", [0.6, 15])
def test_actual_lite_fractional_reach_prefix_and_remainder_are_charged(tmp_path, distance):
    value = hostile_lite_case(
        tmp_path / "fractional-prefix.sqlite3", guard_position=[5.7, 0, 0]
    )
    try:
        qualify_actual_guard_trigger(value)
        before = value.snapshot()
        scene = before.state[NS + ".state"]["spatial_scene"]
        hit = SpatialEngine(scene).query({
            "kind": "body_proximity", "map_id": "lite", "moving_entity_id": "hero",
            "fixed_entity_id": "guard", "destination": [-distance, 0, 0], "radius": 5,
        }).as_payload()
        prefix = hit["exit"] * distance
        assert hit["hit"] is True and 0 < prefix < 0.5
        queued = compose(value, [lite_step(
            value, "fraction", "move-walk", destination=[-distance, 0, 0]
        )])
        paused = execute(value, queued)
        assert paused["status"] == "paused"
        rows = assert_committed(paused, ["fraction"])
        partial = value.snapshot()
        assert thaw_json_value(hero_position(value)) == pytest.approx(
            [-prefix, 0, 0], rel=0, abs=NATIVE_BUDGET_ABS_TOLERANCE_FT
        )
        assert partial.state[NS + ".combat"]["movement_spent"] == pytest.approx(
            prefix, rel=0, abs=NATIVE_BUDGET_ABS_TOLERANCE_FT
        )
        assert partial.state[NS + ".combat"]["movement_spent"] > 0
        assert partial.state[NS + ".combat"]["movement_remaining"] == (
            30 - partial.state[NS + ".combat"]["movement_spent"]
        )
        assert partial.state[NS + ".state"]["coordinate_fractional_prefix_actor"] is None
        pending = partial.state[NS + ".state"]["pending_srd_movement"]
        assert pending["full_intent_validated"] is True
        assert pending["destination"] == [-distance, 0, 0]
        receipt = next(row for row in partial.actions if row.action_id == rows[0]["operation_id"])
        assert receipt.result["context"]["_scene_movement_cost"] == (
            partial.state[NS + ".combat"]["movement_spent"]
        )
        assert receipt.result["context"]["_movement_remaining_after"] == (
            partial.state[NS + ".combat"]["movement_remaining"]
        )
        value.reopen()
        assert execute(value, queued) == paused
        assert value.snapshot() == partial
        decision = dict(GUARD_SPEAR_ATTACK)
        decision.pop("attack_distance")
        decision["take_reaction"] = False
        value.act("prepare_srd_coordinate_opportunity", **decision)
        prepared = value.snapshot()
        for key in ("movement_remaining", "movement_spent"):
            assert prepared.state[NS + ".combat"][key] == partial.state[NS + ".combat"][key]
        value.reopen()
        finished, command = value.act("complete_srd_coordinate_movement", continue_movement=True)
        final = value.snapshot()
        assert hero_position(value) == [-distance, 0, 0]
        assert final.state[NS + ".combat"]["movement_spent"] == distance
        assert final.state[NS + ".combat"]["movement_remaining"] == 30 - distance
        assert final.state[NS + ".state"]["coordinate_fractional_prefix_actor"] is None
        assert final.state[NS + ".state"]["pending_srd_movement"] is None
        value.reopen()
        assert value.execute(command) == finished
        assert value.snapshot() == final
    finally:
        value.close()


def test_actual_lite_subminimum_and_full_excess_cannot_charge_fractional_prefix(tmp_path):
    value = hostile_lite_case(
        tmp_path / "fractional-denials.sqlite3", guard_position=[5.7, 0, 0]
    )
    try:
        before = value.snapshot()
        value.reject("spend_combat_movement", {
            "actor": "hero", "actor_state": "hero", "distance": 0.1,
            "difficult_terrain": False,
        })
        assert value.snapshot() == before
        for identity, destination in (
            ("raw-subminimum", [0, 0.1, 0]), ("full-excess", [-31, 0, 0])
        ):
            before = value.snapshot()
            queued = compose(value, [lite_step(
                value, identity, "move-walk", destination=destination
            )], sequence_id=identity)
            failed = execute(value, queued)
            assert failed["status"] == "failed"
            assert not failed["committed_action_ids"]
            receipt = failed["steps"][0]["receipt"]
            assert receipt is None or receipt["status"] == "failed"
            after = value.snapshot()
            assert after.state == before.state
            assert after.participants == before.participants
            assert hero_position(value) == [0, 0, 0]
            assert after.state[NS + ".combat"]["movement_spent"] == 0
            assert after.state[NS + ".combat"]["movement_remaining"] == 30
            assert after.state[NS + ".state"].get("pending_srd_movement") is None
            value.reopen()
            assert value.snapshot() == after
    finally:
        value.close()


@pytest.mark.parametrize("concentrating", [True, False])
def test_actual_lite_staged_npc_concentration_receipt_is_saved_once(
    tmp_path, monkeypatch, concentrating
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.runtime import _effect_executor

    giant = "qa-giant"
    scene_giant = {
        "id": giant, "map_id": "lite", "position": [5, 0, 0],
        "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
        "height": 6, "facing": [-1, 0], "active": True, "movement": "blocking",
        "visibility": "opaque", "metadata": {
            "participant_ref": giant, "plane": "material", "lighting": "bright",
            "creature_type": "giant", "player": {"label": "QA Giant", "kind": "unit"},
        },
    }
    value = ImportedCase(
        tmp_path / "concentration-receipt.sqlite3", "bless",
        archive_bytes=_native_lite_archive(),
        participant_changes={"party_role": "player_character"},
        entity_changes={"hero": {
            "hp": 200, "max_hp": 200, "armor_class": 1, "initiative_modifier": 0,
            "conditions": ["restrained"], "temp_hp": 0,
        }},
        positions={"hero": [0, 0, 0], "guard": [50, 50, 0],
                   "other": [-50, -50, 0], "extra": [60, 60, 0]},
        scene_objects=[scene_giant],
    )
    try:
        assert giant not in value.entities()
        guard_before = value.entities()["guard"]
        value.act(
            "prepare_srd_creature", creature_id=giant, name="QA Giant",
            source=("Explicit synthetic GM concentration fixture: medium giant STR28, PB2; "
                    "unarmed attack+11, damage1+9 bludgeoning, AC15, HP100, speed30, "
                    "reach5, initiative20; other ability scores10; no weapon or multiattack."),
            armor_class=15, hit_points=100, speed=30, initiative_modifier=20,
            size_rank=2, creature_type="giant", strength_score=28, dexterity_score=10,
            constitution_score=10, intelligence_score=10, wisdom_score=10,
            charisma_score=10, proficiency_bonus=2, melee_reach=5,
            constitution_save=0, strength_save=9, dexterity_save=0,
            intelligence_save=0, wisdom_save=0, charisma_save=0,
        )
        assert value.entities()["guard"] == guard_before
        if concentrating:
            value.cast(targets=["hero"])
            assert value.entities()["hero"]["concentration_spell_id"] == "bless"
        value.act(
            "start_combat_encounter_entities", first_actor="hero", second_actor=giant,
            first_surprised=False, second_surprised=False, tie_winner="second",
        )
        value.act("begin_combat_turn")
        before = value.snapshot()
        assert before.state[NS + ".combat"]["active_actor"] == giant
        calls = []
        d20_effects = []
        native_call = _effect_executor._EFFECT_HANDLERS["call"]
        native_dice = _effect_executor._EFFECT_HANDLERS["dice"]

        def observe_dice(body, effect, context, executor):
            result = native_dice(body, effect, context, executor)
            roll = context.vars[effect.get("store_as", "_roll_result")]
            if roll.sides == 20:
                d20_effects.append(roll.to_dict())
            return result

        def observe(body, effect, context, executor):
            # Observe the real native dispatch; do not replace results, rolls or effects.
            if body in {"d20_test", "resolve_srd_concentration_after_damage"}:
                calls.append(body)
            return native_call(body, effect, context, executor)

        monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
        monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "dice", observe_dice)
        parameters = {
            "target": "hero", "attack_name": "Unarmed strike", "attack_modifier": 11,
            "damage_expression": "fixed-1", "damage_modifier": 9,
            "damage_type": "bludgeoning", "attack_uses_weapon": False, "use_multiattack": False,
            "attack_mode": "melee", "attack_distance": 5, "normal_range": 5, "long_range": 5,
            "actor_can_see_target": True, "target_can_see_actor": True,
            "target_is_charmer": False, "fear_source_in_sight": False,
            "advantage": False, "disadvantage": False,
        }
        mechanics = StatelessMcpMechanics(value.owner)
        command = value.command("begin_srd_creature_attack", parameters, "one-native-attack")
        begun = mechanics.execute_action(command)
        assert begun.status.value == "committed", begun.result
        pending_id = begun.result["context"]["_pending_action_id"]
        assert value.entities()["hero"]["hp"] == 200
        staged = value.snapshot()
        value.reopen()
        mechanics = StatelessMcpMechanics(value.owner)
        assert mechanics.execute_action(command) == begun
        assert value.snapshot() == staged
        completion = value.command(
            "complete_srd_pending_action", {"pending_action_id": pending_id},
            "one-native-completion",
        )
        finished = mechanics.execute_action(completion)
        assert finished.status.value == "committed", finished.result
        final = value.snapshot()
        hero = final.state[NS + ".entities"]["hero"]
        assert hero["hp"] == 190
        assert hero["resources"] == before.state[NS + ".entities"]["hero"]["resources"]
        assert final.state[NS + ".entities"][giant]["hp"] == 100
        assert not final.state[NS + ".state"].get("pending_srd_action_stack")
        assert len(final.actions) == len(before.actions) + 2
        assert calls.count("resolve_srd_concentration_after_damage") == int(concentrating)
        # Attack uses its native dice effect; only the save calls d20_test.
        assert calls.count("d20_test") == int(concentrating)
        assert len(d20_effects) == 1 + int(concentrating)
        assert d20_effects[0] == thaw_json_value(finished.result["context"]["_attack_roll"])
        context = finished.result["context"]
        card = finished.result["result_card"]
        assert card is not None
        concentration_rows = [row for row in card["details"]
                              if row.get("label", "").startswith("Concentration")]
        if concentrating:
            assert context["_concentration_save_dc"] == 10
            roll = context["_concentration_save_roll"]
            assert d20_effects[1] == thaw_json_value(roll)
            assert roll["sides"] == 20 and roll["count"] == 1
            assert len(roll["results"]) == 1 and 1 <= roll["total"] <= 20
            assert roll["total"] == roll["results"][0]
            total = context["_concentration_save_total"]
            modifier = context["_concentration_save_modifier"]
            assert total == roll["total"] + modifier
            outcome = context["_concentration_save_outcome"]
            assert outcome in {"success", "failure"}
            assert (outcome == "success") == (total >= 10)
            assert bool(hero.get("concentration_spell_id")) == (outcome == "success")
            if outcome == "success":
                assert hero["concentration_spell_id"] == "bless"
            assert concentration_rows == [
                {"label": "Concentration DC", "value": 10, "emphasis": False},
                {"label": "Concentration", "value": outcome, "emphasis": False,
                 "equation": {"die_sides": 20, "roll": roll["total"],
                              "modifier": modifier, "total": total}},
            ]
            assert concentration_rows[1] in card["equations"]
        else:
            assert not hero.get("concentration_spell_id")
            assert all(context.get("_concentration_save_" + part) is None
                       for part in ("dc", "roll", "outcome", "modifier", "total"))
            assert not concentration_rows
        calls_before_replay = list(calls)
        dice_before_replay = list(d20_effects)
        value.reopen()
        mechanics = StatelessMcpMechanics(value.owner)
        assert mechanics.execute_action(command) == begun
        assert mechanics.execute_action(completion) == finished
        assert value.snapshot() == final
        assert calls == calls_before_replay
        assert d20_effects == dice_before_replay
    finally:
        value.close()
