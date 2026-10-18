from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import threading
import zipfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Never, cast

import pytest

import sylvia_gm.application.authority as application_authority
import sylvia_gm.infrastructure.sqlite_authority as sqlite_authority
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    CatalogTool,
)
from sylvia_gm.domain.authority import (
    ActionStatus,
    CampaignRecord,
    CanonicalEventType,
    FrozenJsonList,
    JsonValue,
    LifecycleState,
    MechanicContext,
    MechanicOutcome,
    PackageDependency,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    Participant,
    StateMutation,
    StateValue,
    WriterRole,
    thaw_json_value,
    validate_authority_identity,
)
from sylvia_gm.domain.errors import (
    AuthorityError,
    CommandRejected,
    IdempotencyConflict,
    OwnerConflict,
    SchemaIdentityError,
    StaleRevision,
    WrongOwner,
    WrongWriter,
)
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore

SYNTHETIC_SHA = "a" * 64


def _increment_counter(context: MechanicContext) -> MechanicOutcome:
    amount = context.parameters["amount"]
    assert isinstance(amount, int)
    before = context.state.get("clockwork.counter", 0)
    assert isinstance(before, int)
    after = before + amount
    return MechanicOutcome(
        result={"status": "committed", "counter": after},
        mutations=(StateMutation("clockwork", "counter", after),),
        consequences=({"kind": "counter_advanced", "amount": amount},),
    )


def _synthetic_lock() -> PackageLock:
    return PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.clockwork",
                version="1.0.0",
                sha256=SYNTHETIC_SHA,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(),
                capabilities=("synthetic.counter.increment",),
            ),
        )
    )


def _no_rules_lock() -> PackageLock:
    return PackageLock(entries=())


def _counter_schema() -> Mapping[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["amount"],
        "properties": {"amount": {"type": "integer", "minimum": 1}},
    }


def _open_counter_owner(database: Path, owner_id: str = "owner-alpha") -> AuthorityOwner:
    owner = AuthorityOwner.open(database, owner_id=owner_id)
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="increment_counter",
            capability="synthetic.counter.increment",
            input_schema=_counter_schema(),
            execute=_increment_counter,
        )
    )
    return owner


def _create_counter_campaign(
    owner: AuthorityOwner,
    *,
    turn_id: str = "turn-clockwork",
    campaign_id: str | None = None,
) -> CampaignRecord:
    return owner.create_campaign(
        CampaignCreate(
            display_name="Clockwork authority probe",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id=turn_id,
            campaign_id=campaign_id,
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    )


def _counter_action(
    campaign_id: str,
    *,
    action_id: str,
    turn_id: str = "turn-clockwork",
    owner_id: str = "owner-alpha",
    writer: WriterRole = WriterRole.DM,
    expected_revision: int = 0,
    amount: int = 1,
) -> ActionCommand:
    return ActionCommand(
        campaign_id=campaign_id,
        action_id=action_id,
        idempotency_key=f"idem-{action_id}",
        owner_id=owner_id,
        writer=writer,
        expected_revision=expected_revision,
        turn_id=turn_id,
        package_id="synthetic.clockwork",
        tool_name="increment_counter",
        parameters={"amount": amount},
    )


def _f1_package_archive(
    package_id: str,
    version: str,
    capabilities: tuple[str, ...],
    tools: tuple[CatalogTool, ...],
) -> tuple[str, bytes]:
    payload = canonical_json_bytes({"fixture": "synthetic-f1", "package": package_id})
    file_path = "payload/f1.json"
    member_path = f"packages/{package_id}/{file_path}"
    draft: dict[str, object] = {
        "id": package_id,
        "version": version,
        "role": "primary",
        "metadata": {"label": "Synthetic F1 fixture"},
        "provenance": {
            "publisher": "sylvia-gm-tests",
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        },
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": list(capabilities),
        "tools": [
            {
                "name": tool.name,
                "capability": tool.capability,
                "input_schema": thaw_json_value(tool.input_schema),
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["builder", "dm"],
            }
            for tool in sorted(tools, key=lambda item: item.name)
            if tool.capability in capabilities
        ],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": file_path,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    root = content_root_for_record(draft)
    record = {**draft, "content_sha256": root}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 1, "packages": [record]}))
        archive.writestr(member_path, payload)
    return root, stream.getvalue()


def _f1_closure_archive(
    package_lock: PackageLock, tools: tuple[CatalogTool, ...]
) -> tuple[bytes, Mapping[tuple[str, str, str], str]]:
    roots: dict[tuple[str, str, str], str] = {}
    records: list[tuple[dict[str, object], str, bytes]] = []
    for entry in reversed(package_lock.entries):
        payload = canonical_json_bytes({"fixture": "synthetic-f1", "package": entry.package_id})
        file_path = "payload/f1.json"
        matching = tuple(
            tool
            for tool in tools
            if tool.package_id == entry.package_id
            and tool.package_version == entry.version
            and tool.capability in entry.capabilities
        )
        dependencies = [
            {
                "namespace": dependency.namespace,
                "kind": dependency.kind,
                "id": dependency.package_id,
                "version": dependency.version,
                "content_sha256": roots[
                    (
                        dependency.package_id,
                        dependency.version,
                        dependency.sha256,
                    )
                ],
            }
            for dependency in entry.dependencies
        ]
        draft: dict[str, object] = {
            "id": entry.package_id,
            "version": entry.version,
            "role": entry.role.value,
            "metadata": {"label": "Synthetic F1 fixture"},
            "provenance": {
                "publisher": "sylvia-gm-tests",
                "source_sha256": hashlib.sha256(payload).hexdigest(),
            },
            "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
            "minimum_model_capability_tier": "sylvia.capability.t00",
            "dependencies": dependencies,
            "capabilities": list(entry.capabilities),
            "tools": [
                {
                    "name": tool.name,
                    "capability": tool.capability,
                    "input_schema": thaw_json_value(tool.input_schema),
                    "state_predicates": [],
                    "turn_statuses": ["open"],
                    "writers": ["builder", "dm"],
                }
                for tool in sorted(matching, key=lambda item: item.name)
            ],
            "prompt_fragments": [],
            "ui_surfaces": [],
            "files": [
                {
                    "path": file_path,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }
        root = content_root_for_record(draft)
        roots[(entry.package_id, entry.version, entry.sha256)] = root
        records.append(
            (
                {**draft, "content_sha256": root},
                f"packages/{entry.package_id}/{file_path}",
                payload,
            )
        )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 1, "packages": [record for record, _, _ in records]}),
        )
        for _, member_path, payload in records:
            archive.writestr(member_path, payload)
    return stream.getvalue(), roots


@pytest.fixture(autouse=True)
def _admit_f1_synthetic_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run inherited F1 probes over explicit published synthetic F3 package bytes."""

    original_create = AuthorityOwner.create_campaign

    def capture_binding(owner: AuthorityOwner, tool: CatalogTool) -> None:
        captured_schema = application_authority.owned_payload(tool.input_schema)
        captured = replace(tool, input_schema=captured_schema)
        key = (
            captured.package_id,
            captured.package_version,
            captured.package_sha256,
            captured.name,
        )
        existing = owner._tools.get(key)
        if existing is not None and existing != captured:
            raise ValueError("A different mechanic is already registered for this package tool")
        owner._tools[key] = captured

    def create_with_published_package(
        owner: AuthorityOwner, command: CampaignCreate
    ) -> CampaignRecord:
        global SYNTHETIC_SHA
        entries = command.package_lock.entries
        if not entries or sum(entry.role is PackageRole.PRIMARY for entry in entries) != 1:
            return original_create(owner, command)
        archive, roots = _f1_closure_archive(command.package_lock, tuple(owner._tools.values()))
        install_id = f"f1-{hashlib.sha256(archive).hexdigest()[:24]}"
        try:
            candidate = owner.package_candidate(f"candidate-{install_id}")
        except CommandRejected:
            candidate = owner.install_package_archive(io.BytesIO(archive), install_id=install_id)
        rebound: dict[tuple[str, str, str, str], CatalogTool] = {}
        for key, tool in tuple(owner._tools.items()):
            matching_root = next(
                (
                    root
                    for (package_id, version, old_sha), root in roots.items()
                    if package_id == tool.package_id
                    and version == tool.package_version
                    and old_sha == tool.package_sha256
                ),
                None,
            )
            if matching_root is not None:
                owner._tools.pop(key)
                actual = replace(tool, package_sha256=matching_root)
                rebound[
                    (
                        actual.package_id,
                        actual.package_version,
                        actual.package_sha256,
                        actual.name,
                    )
                ] = actual
        owner._tools.update(rebound)
        for (package_id, _version, _old_sha), root in roots.items():
            if package_id == "synthetic.clockwork":
                SYNTHETIC_SHA = root
        return original_create(owner, replace(command, package_lock=candidate.package_lock))

    monkeypatch.setattr(AuthorityOwner, "_register_tool", capture_binding)
    monkeypatch.setattr(AuthorityOwner, "create_campaign", create_with_published_package)


def test_f1_neutral_action_is_atomic_idempotent_and_ruleset_neutral(tmp_path: Path) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-alpha")
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="increment_counter",
            capability="synthetic.counter.increment",
            input_schema=_counter_schema(),
            execute=_increment_counter,
        )
    )

    no_rules_campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Narrative only",
            writer=WriterRole.DM,
            package_lock=_no_rules_lock(),
            turn_id="turn-no-rules-1",
        )
    )
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Clockwork garden",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-clockwork-1",
            participants=(
                Participant(
                    participant_id="gardener",
                    kind="caretaker",
                    package_id="synthetic.clockwork",
                    package_version="1.0.0",
                    data={"label": "Garden keeper"},
                ),
            ),
            initial_state=(StateValue("clockwork", "counter", 2),),
        )
    )
    before = owner.snapshot(campaign.campaign_id)

    command = ActionCommand(
        campaign_id=campaign.campaign_id,
        action_id="action-increment-1",
        idempotency_key="idem-increment-1",
        owner_id="owner-alpha",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="turn-clockwork-1",
        package_id="synthetic.clockwork",
        tool_name="increment_counter",
        parameters={"amount": 3},
    )
    first = owner.execute_action(command)
    after_first = owner.snapshot(campaign.campaign_id)
    duplicate = owner.execute_action(command)
    after_duplicate = owner.snapshot(campaign.campaign_id)

    event_types = tuple(event.event_type for event in after_first.events)
    print("F1 CAMPAIGN", campaign.campaign_id)
    print("F1 REVISION", before.revision, "->", after_first.revision)
    print("F1 WRITER", after_first.writer.value)
    print("F1 TURN/ACTION", command.turn_id, command.action_id)
    print("F1 TOOL CALL", after_first.events[1].safe_payload)
    print("F1 TOOL RESULT", after_first.events[2].safe_payload)
    print(
        "F1 MUTATIONS",
        len(before.mutations),
        len(after_first.mutations),
        len(after_duplicate.mutations),
    )
    print(
        "F1 CANONICAL SEQUENCE",
        [(event.sequence, event.event_type.value) for event in after_first.events],
    )
    print("F1 PACKAGE LOCK", after_first.package_lock)
    print("F1 NO-RULESET TOOLS", owner.catalog(no_rules_campaign.campaign_id))

    assert first == duplicate
    assert first.result == {"status": "committed", "counter": 5}
    assert first.resulting_revision == 1
    assert after_first == after_duplicate
    assert after_first.state == {"clockwork.counter": 5}
    assert event_types == (
        CanonicalEventType.ACTION_DECLARED,
        CanonicalEventType.TOOL_CALL,
        CanonicalEventType.TOOL_RESULT,
        CanonicalEventType.STATE_MUTATION,
        CanonicalEventType.CONSEQUENCE,
        CanonicalEventType.ACTION_COMMITTED,
    )
    assert [event.sequence for event in after_first.events] == list(range(1, 7))
    assert len(after_first.mutations) == 1
    assert len(after_first.consequences) == 1
    assert after_first.package_lock == _synthetic_lock()
    assert owner.catalog(no_rules_campaign.campaign_id) == ()
    owner.close()


def test_wrong_application_owner_is_rejected_without_authoritative_effect(tmp_path: Path) -> None:
    owner = AuthorityOwner.open(tmp_path / "sylvia-gm.db", owner_id="owner-alpha")
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="increment_counter",
            capability="synthetic.counter.increment",
            input_schema=_counter_schema(),
            execute=_increment_counter,
        )
    )
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Owner fence",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-owner",
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    )
    command = ActionCommand(
        campaign_id=campaign.campaign_id,
        action_id="action-wrong-owner",
        idempotency_key="idem-wrong-owner",
        owner_id="intruder",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="turn-owner",
        package_id="synthetic.clockwork",
        tool_name="increment_counter",
        parameters={"amount": 1},
    )

    with pytest.raises(WrongOwner) as caught:
        owner.execute_action(command)

    snapshot = owner.snapshot(campaign.campaign_id)
    print("F1 WRONG OWNER", caught.value, snapshot.revision, snapshot.events)
    assert caught.value.code == "wrong_owner"
    assert snapshot.revision == 0
    assert snapshot.events == ()
    owner.close()


def test_wrong_campaign_writer_is_rejected_without_authoritative_effect(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = _create_counter_campaign(owner)
    command = _counter_action(
        campaign.campaign_id,
        action_id="wrong-writer",
        writer=WriterRole.BUILDER,
    )

    with pytest.raises(WrongWriter) as caught:
        owner.execute_action(command)

    snapshot = owner.snapshot(campaign.campaign_id)
    print("F1 WRONG WRITER", caught.value, snapshot.writer.value, snapshot.revision)
    assert caught.value.code == "wrong_writer"
    assert snapshot.writer is WriterRole.DM
    assert snapshot.revision == 0
    assert snapshot.events == ()
    owner.close()


def test_stale_expected_revision_is_rejected_without_second_effect(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = _create_counter_campaign(owner)
    owner.execute_action(_counter_action(campaign.campaign_id, action_id="fresh"))

    with pytest.raises(StaleRevision) as caught:
        owner.execute_action(_counter_action(campaign.campaign_id, action_id="stale"))

    snapshot = owner.snapshot(campaign.campaign_id)
    print("F1 STALE REVISION", caught.value, "revision", snapshot.revision)
    assert caught.value.code == "stale_revision"
    assert snapshot.revision == 1
    assert snapshot.state == {"clockwork.counter": 1}
    assert len(snapshot.actions) == 1
    owner.close()


def test_idempotency_key_payload_collision_fails_closed(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = _create_counter_campaign(owner)
    original = _counter_action(campaign.campaign_id, action_id="identity")
    owner.execute_action(original)

    with pytest.raises(IdempotencyConflict) as caught:
        owner.execute_action(replace(original, parameters={"amount": 2}))

    snapshot = owner.snapshot(campaign.campaign_id)
    print("F1 IDEMPOTENCY COLLISION", caught.value, snapshot.state, snapshot.revision)
    assert caught.value.code == "idempotency_conflict"
    assert snapshot.state == {"clockwork.counter": 1}
    assert snapshot.revision == 1
    assert len(snapshot.actions) == 1
    owner.close()


def test_unavailable_package_tool_is_rejected_before_chronology(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Narrative only",
            writer=WriterRole.DM,
            package_lock=_no_rules_lock(),
            turn_id="turn-no-rules",
        )
    )
    command = _counter_action(
        campaign.campaign_id,
        action_id="unavailable-tool",
        turn_id="turn-no-rules",
    )

    with pytest.raises(CommandRejected) as caught:
        owner.execute_action(command)

    snapshot = owner.snapshot(campaign.campaign_id)
    print("F1 UNAVAILABLE TOOL", caught.value, owner.catalog(campaign.campaign_id))
    assert caught.value.code == "tool_unavailable"
    assert snapshot.revision == 0
    assert snapshot.events == ()
    owner.close()


def test_invalid_tool_schema_payload_is_rejected_before_execution(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = _create_counter_campaign(owner)

    with pytest.raises(CommandRejected) as caught:
        owner.execute_action(
            _counter_action(campaign.campaign_id, action_id="invalid-schema", amount=0)
        )

    snapshot = owner.snapshot(campaign.campaign_id)
    print("F1 INVALID SCHEMA", caught.value, snapshot.state, snapshot.revision)
    assert caught.value.code == "invalid_schema"
    assert snapshot.state == {"clockwork.counter": 0}
    assert snapshot.revision == 0
    assert snapshot.events == ()
    owner.close()


def test_inactive_campaign_rejects_mechanics_without_chronology(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Archived clockwork",
            writer=WriterRole.NONE,
            package_lock=_synthetic_lock(),
            turn_id="turn-archived",
            lifecycle=LifecycleState.ARCHIVED,
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    )

    with pytest.raises(CommandRejected) as caught:
        owner.execute_action(
            _counter_action(
                campaign.campaign_id,
                action_id="inactive",
                turn_id="turn-archived",
            )
        )

    snapshot = owner.snapshot(campaign.campaign_id)
    print("F1 INACTIVE", caught.value, snapshot.lifecycle.value, snapshot.revision)
    assert caught.value.code == "lifecycle_inactive"
    assert snapshot.revision == 0
    assert snapshot.events == ()
    owner.close()


def test_cross_campaign_participant_reference_is_rejected(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="increment_for_participant",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["target", "amount"],
                "properties": {
                    "target": {
                        "type": "string",
                        "x-sylvia-gm-reference": "participant",
                    },
                    "amount": {"type": "integer", "minimum": 1},
                },
            },
            execute=_increment_counter,
        )
    )
    first = owner.create_campaign(
        CampaignCreate(
            display_name="First campaign",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-first",
            participants=(
                Participant("first-person", "actor", "synthetic.clockwork", "1.0.0", {}),
            ),
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    )
    second = owner.create_campaign(
        CampaignCreate(
            display_name="Second campaign",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-second",
            participants=(
                Participant("second-person", "actor", "synthetic.clockwork", "1.0.0", {}),
            ),
        )
    )
    command = ActionCommand(
        campaign_id=first.campaign_id,
        action_id="cross-campaign-reference",
        idempotency_key="idem-cross-campaign-reference",
        owner_id="owner-alpha",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="turn-first",
        package_id="synthetic.clockwork",
        tool_name="increment_for_participant",
        parameters={"target": "second-person", "amount": 1},
    )

    with pytest.raises(CommandRejected) as caught:
        owner.execute_action(command)

    first_snapshot = owner.snapshot(first.campaign_id)
    second_snapshot = owner.snapshot(second.campaign_id)
    print("F1 CROSS CAMPAIGN", caught.value, first_snapshot.revision, second_snapshot.revision)
    assert caught.value.code == "cross_campaign_reference"
    assert first_snapshot.state == {"clockwork.counter": 0}
    assert first_snapshot.revision == second_snapshot.revision == 0
    assert first_snapshot.events == second_snapshot.events == ()
    owner.close()


def test_accepted_tool_failure_commits_only_safe_canonical_chronology(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")

    def fail_with_sensitive_diagnostics(_context: MechanicContext) -> MechanicOutcome:
        raise RuntimeError("credential=SECRET private_reasoning=never-store")

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="fail_safely",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
            execute=fail_with_sensitive_diagnostics,
        )
    )
    campaign = _create_counter_campaign(owner)
    command = replace(
        _counter_action(campaign.campaign_id, action_id="safe-failure"),
        tool_name="fail_safely",
        parameters={},
    )

    receipt = owner.execute_action(command)

    snapshot = owner.snapshot(campaign.campaign_id)
    serialized_events = repr(snapshot.events)
    print(
        "F1 SAFE FAILURE",
        receipt.status.value,
        [(event.sequence, event.event_type.value, event.safe_payload) for event in snapshot.events],
    )
    assert receipt.status is ActionStatus.FAILED
    assert receipt.result == {"status": "failed", "code": "tool_execution_failed"}
    assert snapshot.revision == 1
    assert snapshot.state == {"clockwork.counter": 0}
    assert snapshot.mutations == ()
    assert snapshot.consequences == ()
    assert tuple(event.event_type for event in snapshot.events) == (
        CanonicalEventType.ACTION_DECLARED,
        CanonicalEventType.TOOL_CALL,
        CanonicalEventType.TOOL_RESULT,
        CanonicalEventType.ACTION_FAILED,
    )
    assert "SECRET" not in serialized_events
    assert "private_reasoning" not in serialized_events
    assert "never-store" not in serialized_events
    owner.close()


def test_successful_mechanic_private_channels_become_one_safe_failed_action(
    tmp_path: Path,
) -> None:
    sentinel = "SENSITIVE_MECHANIC_SENTINEL"
    unsafe_cases = (
        (
            "result-private-reasoning",
            MechanicOutcome(result={"status": "committed", "private_reasoning": sentinel}),
            ("private_reasoning",),
        ),
        (
            "nested-result-diagnostic",
            MechanicOutcome(result={"status": "committed", "details": {"diagnostic": sentinel}}),
            ("diagnostic",),
        ),
        (
            "consequence-traceback",
            MechanicOutcome(
                result={"status": "committed"},
                consequences=({"kind": "unsafe", "details": {"traceback": sentinel}},),
            ),
            ("traceback",),
        ),
        (
            "mutation-raw-reasoning-value",
            MechanicOutcome(
                result={"status": "committed"},
                mutations=(
                    StateMutation(
                        "clockwork",
                        "counter",
                        {"raw_reasoning": sentinel},
                    ),
                ),
            ),
            ("raw_reasoning",),
        ),
    )

    for case_index, (case_name, unsafe_outcome, unsafe_keys) in enumerate(unsafe_cases):
        database = tmp_path / f"{case_name}.db"
        owner = _open_counter_owner(database)

        def return_unsafe_outcome(
            _context: MechanicContext,
            outcome: MechanicOutcome = unsafe_outcome,
        ) -> MechanicOutcome:
            return outcome

        owner.register_tool(
            CatalogTool(
                package_id="synthetic.clockwork",
                package_version="1.0.0",
                package_sha256=SYNTHETIC_SHA,
                name="canonical_safety_probe",
                capability="synthetic.counter.increment",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [],
                    "properties": {},
                },
                execute=return_unsafe_outcome,
            )
        )
        campaign = _create_counter_campaign(owner)
        command = replace(
            _counter_action(campaign.campaign_id, action_id=f"unsafe-case-{case_index}"),
            tool_name="canonical_safety_probe",
            parameters={},
        )

        receipt = owner.execute_action(command)
        before_close = owner.snapshot(campaign.campaign_id)
        owner.close()

        fresh_owner = _open_counter_owner(database, owner_id="owner-reloaded")
        reloaded = fresh_owner.snapshot(campaign.campaign_id)
        replayed = fresh_owner.execute_action(replace(command, owner_id="owner-reloaded"))
        after_replay = fresh_owner.snapshot(campaign.campaign_id)
        fresh_owner.close()

        print(
            "F1 UNSAFE OUTCOME SAFE FAILURE",
            case_name,
            receipt.status.value,
            [(event.sequence, event.event_type.value) for event in reloaded.events],
            reloaded.revision,
        )
        assert receipt.status is ActionStatus.FAILED
        assert receipt.result == {"status": "failed", "code": "tool_execution_failed"}
        assert replayed == receipt
        assert before_close == reloaded == after_replay
        assert reloaded.revision == 1
        assert reloaded.state == {"clockwork.counter": 0}
        assert reloaded.mutations == ()
        assert reloaded.consequences == ()
        assert tuple(event.event_type for event in reloaded.events) == (
            CanonicalEventType.ACTION_DECLARED,
            CanonicalEventType.TOOL_CALL,
            CanonicalEventType.TOOL_RESULT,
            CanonicalEventType.ACTION_FAILED,
        )
        serialized_canonical = repr((receipt, replayed, reloaded))
        for forbidden in (sentinel, *unsafe_keys):
            assert forbidden not in serialized_canonical
            assert forbidden.encode() not in database.read_bytes()

    parameter_database = tmp_path / "reserved-parameter.db"
    parameter_owner = _open_counter_owner(parameter_database)
    parameter_executed = False

    def would_accept_reserved_parameter(_context: MechanicContext) -> MechanicOutcome:
        nonlocal parameter_executed
        parameter_executed = True
        return MechanicOutcome(result={"status": "committed"})

    parameter_owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="parameter_safety_probe",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["diagnostic"],
                "properties": {"diagnostic": {"type": "string"}},
            },
            execute=would_accept_reserved_parameter,
        )
    )
    parameter_campaign = _create_counter_campaign(parameter_owner)
    parameter_command = replace(
        _counter_action(parameter_campaign.campaign_id, action_id="reserved-parameter"),
        tool_name="parameter_safety_probe",
        parameters={"diagnostic": sentinel},
    )

    with pytest.raises(CommandRejected) as caught:
        parameter_owner.execute_action(parameter_command)

    parameter_snapshot = parameter_owner.snapshot(parameter_campaign.campaign_id)
    parameter_owner.close()
    print(
        "F1 RESERVED TOOL PARAMETER REJECTED",
        caught.value.code,
        parameter_snapshot.revision,
        parameter_executed,
    )
    assert caught.value.code == "unsafe_canonical_payload"
    assert parameter_executed is False
    assert parameter_snapshot.revision == 0
    assert parameter_snapshot.actions == ()
    assert parameter_snapshot.events == ()
    assert sentinel.encode() not in parameter_database.read_bytes()


def test_mid_transaction_failure_rolls_back_every_authoritative_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = _create_counter_campaign(owner)
    command = _counter_action(campaign.campaign_id, action_id="rollback-probe", amount=4)

    def interrupt_before_commit(_store: SQLiteAuthorityStore) -> None:
        raise RuntimeError("injected commit interruption")

    with monkeypatch.context() as fault:
        fault.setattr(
            SQLiteAuthorityStore,
            "_before_commit",
            interrupt_before_commit,
            raising=False,
        )
        with pytest.raises(RuntimeError, match="injected commit interruption"):
            owner.execute_action(command)

    rolled_back = owner.snapshot(campaign.campaign_id)
    print(
        "F1 ROLLBACK",
        rolled_back.revision,
        rolled_back.state,
        rolled_back.actions,
        rolled_back.events,
    )
    assert rolled_back.revision == 0
    assert rolled_back.state == {"clockwork.counter": 0}
    assert rolled_back.actions == ()
    assert rolled_back.events == ()
    assert rolled_back.mutations == ()
    assert rolled_back.consequences == ()

    retried = owner.execute_action(command)
    after_retry = owner.snapshot(campaign.campaign_id)
    assert retried.resulting_revision == 1
    assert after_retry.state == {"clockwork.counter": 4}
    assert len(after_retry.actions) == 1
    owner.close()


def test_fresh_owner_reload_reconstructs_authority_and_replays_durable_key(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    first_owner = _open_counter_owner(database, owner_id="owner-before-restart")
    campaign = _create_counter_campaign(
        first_owner,
        turn_id="turn-reload",
        campaign_id="campaign-stable-across-reload",
    )
    command = _counter_action(
        campaign.campaign_id,
        action_id="durable-action",
        turn_id="turn-reload",
        owner_id="owner-before-restart",
        amount=7,
    )
    original_receipt = first_owner.execute_action(command)
    before_close = first_owner.snapshot(campaign.campaign_id)
    first_owner.close()

    fresh_owner = _open_counter_owner(database, owner_id="owner-after-restart")
    reloaded = fresh_owner.snapshot(campaign.campaign_id)
    replayed = fresh_owner.execute_action(replace(command, owner_id="owner-after-restart"))
    after_replay = fresh_owner.snapshot(campaign.campaign_id)

    print(
        "F1 RELOAD",
        reloaded.campaign_id,
        reloaded.revision,
        reloaded.state,
        [(event.sequence, event.event_type.value) for event in reloaded.events],
        reloaded.package_lock,
    )
    assert reloaded == before_close
    assert reloaded.campaign_id == "campaign-stable-across-reload"
    assert reloaded.revision == 1
    assert reloaded.state == {"clockwork.counter": 7}
    assert reloaded.package_lock == _synthetic_lock()
    assert replayed == original_receipt
    assert after_replay == reloaded
    fresh_owner.close()


def test_concurrent_same_campaign_commands_serialize_before_revision_admission(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-alpha")
    execution_count = 0
    execution_count_lock = threading.Lock()

    def counted_increment(context: MechanicContext) -> MechanicOutcome:
        nonlocal execution_count
        with execution_count_lock:
            execution_count += 1
        return _increment_counter(context)

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="increment_counter",
            capability="synthetic.counter.increment",
            input_schema=_counter_schema(),
            execute=counted_increment,
        )
    )
    campaign = _create_counter_campaign(owner)
    start_together = threading.Barrier(3)

    def submit(action_id: str) -> object:
        start_together.wait()
        try:
            return owner.execute_action(_counter_action(campaign.campaign_id, action_id=action_id))
        except StaleRevision as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit, action_id) for action_id in ("racer-a", "racer-b")]
        start_together.wait()
        results = [future.result(timeout=5) for future in futures]

    snapshot = owner.snapshot(campaign.campaign_id)
    print(
        "F1 SAME CAMPAIGN CONCURRENCY",
        [type(result).__name__ for result in results],
        snapshot.revision,
        snapshot.state,
    )
    assert sum(isinstance(result, StaleRevision) for result in results) == 1
    assert sum(not isinstance(result, StaleRevision) for result in results) == 1
    assert execution_count == 1
    assert snapshot.revision == 1
    assert snapshot.state == {"clockwork.counter": 1}
    assert len(snapshot.actions) == 1
    assert len(snapshot.events) == 6
    owner.close()


def test_cross_campaign_lanes_state_chronology_and_idempotency_are_isolated(
    tmp_path: Path,
) -> None:
    owner = AuthorityOwner.open(tmp_path / "sylvia-gm.db", owner_id="owner-alpha")
    first_entered_mechanic = threading.Event()
    release_first = threading.Event()

    def independently_scheduled_increment(context: MechanicContext) -> MechanicOutcome:
        if context.campaign_id == "campaign-isolation-a":
            first_entered_mechanic.set()
            assert release_first.wait(timeout=5)
        return _increment_counter(context)

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="increment_counter",
            capability="synthetic.counter.increment",
            input_schema=_counter_schema(),
            execute=independently_scheduled_increment,
        )
    )
    first = _create_counter_campaign(owner, campaign_id="campaign-isolation-a")
    second = _create_counter_campaign(owner, campaign_id="campaign-isolation-b")
    first_command = _counter_action(first.campaign_id, action_id="shared-action", amount=2)
    second_command = _counter_action(second.campaign_id, action_id="shared-action", amount=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        blocked_first = executor.submit(owner.execute_action, first_command)
        assert first_entered_mechanic.wait(timeout=5)
        independent_second = executor.submit(owner.execute_action, second_command)
        second_receipt = independent_second.result(timeout=5)
        assert not blocked_first.done()
        release_first.set()
        first_receipt = blocked_first.result(timeout=5)

    first_snapshot = owner.snapshot(first.campaign_id)
    second_snapshot = owner.snapshot(second.campaign_id)
    print(
        "F1 CROSS CAMPAIGN ISOLATION",
        first_snapshot.state,
        second_snapshot.state,
        len(first_snapshot.events),
        len(second_snapshot.events),
    )
    assert first_receipt.campaign_id == first.campaign_id
    assert second_receipt.campaign_id == second.campaign_id
    assert first_snapshot.state == {"clockwork.counter": 2}
    assert second_snapshot.state == {"clockwork.counter": 5}
    assert first_snapshot.revision == second_snapshot.revision == 1
    assert [event.sequence for event in first_snapshot.events] == list(range(1, 7))
    assert [event.sequence for event in second_snapshot.events] == list(range(1, 7))
    assert all(event.action_id == "shared-action" for event in first_snapshot.events)
    assert all(event.action_id == "shared-action" for event in second_snapshot.events)
    owner.close()


def test_direct_runtime_style_sqlite_open_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CommandRejected) as caught:
        SQLiteAuthorityStore(tmp_path / "sylvia-gm.db")

    print("F1 DIRECT SQLITE BYPASS", caught.value)
    assert caught.value.code == "application_owner_required"


def test_second_owner_process_is_rejected_by_database_lock(tmp_path: Path) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = _open_counter_owner(database)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from sylvia_gm.application.authority import AuthorityOwner; "
                "from sylvia_gm.domain.errors import OwnerConflict; "
                "import sys; "
                "\ntry:\n AuthorityOwner.open(Path(sys.argv[1]), 'second-process')"
                "\nexcept OwnerConflict:\n print('OWNER_CONFLICT'); raise SystemExit(0)"
                "\nelse:\n print('UNEXPECTED_SECOND_OWNER'); raise SystemExit(9)"
            ),
            str(database),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    print("F1 SECOND OWNER", probe.returncode, probe.stdout.strip(), probe.stderr.strip())
    assert probe.returncode == 0
    assert probe.stdout.strip() == "OWNER_CONFLICT"
    owner.close()


def test_successful_no_op_has_paired_tool_chronology_and_no_authoritative_mutation(
    tmp_path: Path,
) -> None:
    owner = AuthorityOwner.open(tmp_path / "sylvia-gm.db", owner_id="owner-alpha")
    seen_context: list[MechanicContext] = []

    def no_op(context: MechanicContext) -> MechanicOutcome:
        seen_context.append(context)
        return MechanicOutcome(result={"status": "no_op", "reason": "already_set"})

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="no_op",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
            execute=no_op,
        )
    )
    campaign = _create_counter_campaign(owner)
    receipt = owner.execute_action(
        replace(
            _counter_action(campaign.campaign_id, action_id="accepted-no-op"),
            tool_name="no_op",
            parameters={},
        )
    )

    snapshot = owner.snapshot(campaign.campaign_id)
    print(
        "F1 NO OP",
        receipt.result,
        [(event.sequence, event.event_type.value) for event in snapshot.events],
    )
    assert receipt.status is ActionStatus.COMMITTED
    assert receipt.result == {"status": "no_op", "reason": "already_set"}
    assert snapshot.revision == 1
    assert snapshot.state == {"clockwork.counter": 0}
    assert snapshot.mutations == ()
    assert snapshot.consequences == ()
    assert tuple(event.event_type for event in snapshot.events) == (
        CanonicalEventType.ACTION_DECLARED,
        CanonicalEventType.TOOL_CALL,
        CanonicalEventType.TOOL_RESULT,
        CanonicalEventType.ACTION_COMMITTED,
    )
    assert set(vars(seen_context[0])) == {
        "campaign_id",
        "turn_id",
        "action_id",
        "state",
        "parameters",
        "participants",
        "choice_resolution",
    }
    assert seen_context[0].participants == {}
    owner.close()


def test_exact_package_lock_round_trips_and_package_identity_is_immutable(
    tmp_path: Path,
) -> None:
    dependency_sha = "b" * 64
    exact_lock = PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.clockwork",
                version="1.0.0",
                sha256=SYNTHETIC_SHA,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(
                    PackageDependency(
                        package_id="synthetic.foundation",
                        version="2.3.4",
                        sha256=dependency_sha,
                        kind="library",
                    ),
                ),
                capabilities=("synthetic.counter.increment",),
            ),
            PackageLockEntry(
                package_id="synthetic.foundation",
                version="2.3.4",
                sha256=dependency_sha,
                role=PackageRole.DEPENDENCY,
                load_order=1,
                dependencies=(),
                capabilities=(),
            ),
        )
    )
    owner = AuthorityOwner.open(tmp_path / "sylvia-gm.db", owner_id="owner-alpha")
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Exact lock",
            writer=WriterRole.DM,
            package_lock=exact_lock,
            turn_id="turn-exact-lock",
        )
    )
    snapshot = owner.snapshot(campaign.campaign_id)

    with pytest.raises(FrozenInstanceError):
        snapshot.package_lock.entries[0].load_order = 9  # type: ignore[misc]

    conflicting_primary = replace(
        exact_lock.entries[0],
        capabilities=("synthetic.counter.increment", "synthetic.conflict"),
    )
    with pytest.raises(CommandRejected) as caught:
        owner.create_campaign(
            CampaignCreate(
                display_name="Conflicting package identity",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=(conflicting_primary, exact_lock.entries[1])),
                turn_id="turn-conflicting-lock",
                campaign_id="campaign-conflicting-lock",
            )
        )

    print("F1 EXACT IMMUTABLE LOCK", snapshot.package_lock, caught.value)
    assert snapshot.package_lock != exact_lock
    assert [entry.package_id for entry in snapshot.package_lock.entries] == [
        "synthetic.clockwork",
        "synthetic.foundation",
    ]
    assert all(len(entry.sha256) == 64 for entry in snapshot.package_lock.entries)
    assert caught.value.code == "archive_invalid"
    assert owner.snapshot(campaign.campaign_id).package_lock == snapshot.package_lock
    owner.close()


def test_action_identity_is_a_durable_replay_key_and_payload_collision_fails_closed(
    tmp_path: Path,
) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = _create_counter_campaign(owner)
    original = _counter_action(campaign.campaign_id, action_id="stable-action-id", amount=3)
    first = owner.execute_action(original)

    replay = owner.execute_action(
        replace(original, idempotency_key="different-transport-idempotency-key")
    )
    with pytest.raises(IdempotencyConflict):
        owner.execute_action(
            replace(
                original,
                idempotency_key="colliding-action-payload",
                parameters={"amount": 4},
            )
        )

    snapshot = owner.snapshot(campaign.campaign_id)
    print("F1 ACTION IDENTITY", first, replay, snapshot.state, snapshot.revision)
    assert replay == first
    assert snapshot.state == {"clockwork.counter": 3}
    assert snapshot.revision == 1
    assert len(snapshot.actions) == 1
    assert len(snapshot.events) == 6
    owner.close()


def test_nested_parameters_use_one_owned_immutable_canonical_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-alpha")
    sentinel = "NESTED_PARAMETER_SENTINEL"
    retained_settings: dict[str, JsonValue] = {"mode": "steady"}
    retained_labels: list[JsonValue] = ["ordinary"]
    caller_parameters: dict[str, JsonValue] = {
        "config": {"settings": retained_settings, "labels": retained_labels}
    }
    mutation_errors: list[type[BaseException]] = []

    def inspect_immutable_parameters(context: MechanicContext) -> MechanicOutcome:
        retained_settings["private_reasoning_payload"] = sentinel
        retained_labels.append(sentinel)
        config = context.parameters["config"]
        assert isinstance(config, Mapping)
        settings = config["settings"]
        labels = config["labels"]
        assert isinstance(settings, Mapping)
        assert isinstance(labels, tuple)
        try:
            settings["mode"] = "mutated"  # type: ignore[index]
        except TypeError as error:
            mutation_errors.append(type(error))
        try:
            labels.append("mutated")  # type: ignore[attr-defined]
        except AttributeError as error:
            mutation_errors.append(type(error))
        mode = settings["mode"]
        assert isinstance(mode, str)
        label_values: list[JsonValue] = []
        for label in labels:
            assert isinstance(label, str)
            label_values.append(label)
        return MechanicOutcome(result={"status": "committed", "mode": mode, "labels": label_values})

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="inspect_nested",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["config"],
                "properties": {"config": {"type": "object"}},
            },
            execute=inspect_immutable_parameters,
        )
    )
    campaign = _create_counter_campaign(owner)
    command = replace(
        _counter_action(campaign.campaign_id, action_id="immutable-parameters"),
        tool_name="inspect_nested",
        parameters=caller_parameters,
    )

    receipt = owner.execute_action(command)
    caller_parameters["config"] = {"private_reasoning_payload": "changed-after-return"}
    replay = owner.execute_action(
        replace(
            command,
            idempotency_key="immutable-parameters-alias",
            parameters={"config": {"settings": {"mode": "steady"}, "labels": ["ordinary"]}},
        )
    )
    snapshot = owner.snapshot(campaign.campaign_id)
    owner.close()

    with sqlite3.connect(database) as connection:
        stored_payload, stored_hash = connection.execute(
            "SELECT payload_json, payload_hash FROM actions WHERE action_id = ?",
            ("immutable-parameters",),
        ).fetchone()
    expected_parameters = {"config": {"settings": {"mode": "steady"}, "labels": ["ordinary"]}}
    semantic_payload = {
        "campaign_id": campaign.campaign_id,
        "action_id": "immutable-parameters",
        "writer": WriterRole.DM.value,
        "expected_revision": 0,
        "turn_id": "turn-clockwork",
        "package_id": "synthetic.clockwork",
        "package_version": "1.0.0",
        "package_sha256": SYNTHETIC_SHA,
        "tool_name": "inspect_nested",
        "parameters": expected_parameters,
    }
    expected_hash = hashlib.sha256(
        json.dumps(
            semantic_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    print(
        "F1 IMMUTABLE PARAMETER SNAPSHOT",
        mutation_errors,
        receipt.result,
        stored_payload,
        stored_hash == expected_hash,
    )
    assert mutation_errors == [TypeError, AttributeError]
    assert receipt == replay
    assert receipt.result == {
        "status": "committed",
        "mode": "steady",
        "labels": ["ordinary"],
    }
    assert json.loads(stored_payload) == expected_parameters
    assert stored_hash == expected_hash
    assert snapshot.events[1].safe_payload["parameters"] == expected_parameters
    assert sentinel.encode() not in database.read_bytes()
    assert b"changed-after-return" not in database.read_bytes()


def test_structural_privacy_channel_families_fail_to_generic_safe_chronology(
    tmp_path: Path,
) -> None:
    sentinel = "STRUCTURAL_CHANNEL_SENTINEL"
    reserved_variants = (
        "private_reasoning_payload",
        "raw_reasoning_text",
        "provider-reasoning-trace",
        "diagnostic_info",
        "tracebackText",
        "inspector_trace_payload",
        "chain reasoning details",
        "hiddenReasoningChannel",
        "private-trace-payload",
        "inspectortracepayload",
        "privatetracepayload",
        "rawreasoningtext",
        "providerreasoningtrace",
        "diagnosticinfo",
        "tracebacktext",
    )

    for index, reserved_name in enumerate(reserved_variants):
        database = tmp_path / f"structural-channel-{index}.db"
        owner = AuthorityOwner.open(database, owner_id="owner-alpha")

        def return_reserved_channel(
            _context: MechanicContext, *, key: str = reserved_name
        ) -> MechanicOutcome:
            return MechanicOutcome(result={"status": "committed", "details": {key: sentinel}})

        owner.register_tool(
            CatalogTool(
                package_id="synthetic.clockwork",
                package_version="1.0.0",
                package_sha256=SYNTHETIC_SHA,
                name="structural_channel_probe",
                capability="synthetic.counter.increment",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [],
                    "properties": {},
                },
                execute=return_reserved_channel,
            )
        )
        campaign = _create_counter_campaign(owner)
        receipt = owner.execute_action(
            replace(
                _counter_action(campaign.campaign_id, action_id=f"structural-{index}"),
                tool_name="structural_channel_probe",
                parameters={},
            )
        )
        snapshot = owner.snapshot(campaign.campaign_id)
        owner.close()

        serialized = repr((receipt, snapshot))
        print(
            "F1 STRUCTURAL CHANNEL REJECTED",
            index,
            receipt.status.value,
            [(event.sequence, event.event_type.value) for event in snapshot.events],
        )
        assert receipt.status is ActionStatus.FAILED
        assert receipt.result == {"status": "failed", "code": "tool_execution_failed"}
        assert snapshot.revision == 1
        assert snapshot.mutations == ()
        assert snapshot.consequences == ()
        assert tuple(event.event_type for event in snapshot.events) == (
            CanonicalEventType.ACTION_DECLARED,
            CanonicalEventType.TOOL_CALL,
            CanonicalEventType.TOOL_RESULT,
            CanonicalEventType.ACTION_FAILED,
        )
        assert reserved_name not in serialized
        assert sentinel not in serialized
        assert reserved_name.encode() not in database.read_bytes()
        assert sentinel.encode() not in database.read_bytes()


def test_neutral_stack_exception_structures_and_safe_text_remain_canonical(
    tmp_path: Path,
) -> None:
    owner = AuthorityOwner.open(tmp_path / "sylvia-gm.db", owner_id="owner-alpha")

    def return_neutral_structures(_context: MechanicContext) -> MechanicOutcome:
        return MechanicOutcome(
            result={
                "status": "committed",
                "stack": ["tile-a", "tile-b"],
                "exception": {"kind": "festival-day"},
                "trace": "ordinary route",
                "inspector": "ordinary role",
                "private": "ordinary property",
                "note": "diagnostic and private_reasoning are ordinary words here",
            },
            mutations=(
                StateMutation(
                    "stack",
                    "exception",
                    {"note": "tracebackText is safe text under a neutral key"},
                ),
            ),
            consequences=({"stack": 2, "exception": "none"},),
        )

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="neutral_structure_probe",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
            execute=return_neutral_structures,
        )
    )
    campaign = _create_counter_campaign(owner)
    receipt = owner.execute_action(
        replace(
            _counter_action(campaign.campaign_id, action_id="neutral-structures"),
            tool_name="neutral_structure_probe",
            parameters={},
        )
    )
    snapshot = owner.snapshot(campaign.campaign_id)
    owner.close()

    print("F1 NEUTRAL STRUCTURES", receipt.status.value, receipt.result, snapshot.state)
    assert receipt.status is ActionStatus.COMMITTED
    assert receipt.result["stack"] == ["tile-a", "tile-b"]
    assert receipt.result["exception"] == {"kind": "festival-day"}
    assert receipt.result["trace"] == "ordinary route"
    assert receipt.result["inspector"] == "ordinary role"
    assert receipt.result["private"] == "ordinary property"
    assert snapshot.state["stack.exception"] == {
        "note": "tracebackText is safe text under a neutral key"
    }
    assert snapshot.consequences[0].payload == {"stack": 2, "exception": "none"}
    assert snapshot.events[-1].event_type is CanonicalEventType.ACTION_COMMITTED


def test_malformed_mechanic_result_commits_one_generic_safe_failed_action(tmp_path: Path) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-alpha")
    executed = False
    sentinel = "MALFORMED_RESULT_SENTINEL"

    def return_malformed_result(_context: MechanicContext) -> MechanicOutcome:
        nonlocal executed
        executed = True
        return {"private_reasoning_payload": sentinel}  # type: ignore[return-value]

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="malformed_result_probe",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
            execute=return_malformed_result,
        )
    )
    campaign = _create_counter_campaign(owner)
    receipt = owner.execute_action(
        replace(
            _counter_action(campaign.campaign_id, action_id="malformed-result"),
            tool_name="malformed_result_probe",
            parameters={},
        )
    )
    snapshot = owner.snapshot(campaign.campaign_id)
    owner.close()

    serialized = repr((receipt, snapshot))
    print(
        "F1 MALFORMED RESULT SAFE FAILURE",
        executed,
        receipt.status.value,
        [(event.sequence, event.event_type.value) for event in snapshot.events],
    )
    assert executed is True
    assert receipt.status is ActionStatus.FAILED
    assert receipt.result == {"status": "failed", "code": "tool_execution_failed"}
    assert snapshot.revision == 1
    assert snapshot.mutations == ()
    assert snapshot.consequences == ()
    assert tuple(event.event_type for event in snapshot.events) == (
        CanonicalEventType.ACTION_DECLARED,
        CanonicalEventType.TOOL_CALL,
        CanonicalEventType.TOOL_RESULT,
        CanonicalEventType.ACTION_FAILED,
    )
    assert "private_reasoning_payload" not in serialized
    assert sentinel not in serialized
    assert sentinel.encode() not in database.read_bytes()


def test_campaign_creation_rejects_unsafe_payloads_atomically_and_keeps_neutral_state(
    tmp_path: Path,
) -> None:
    sentinel = "UNSAFE_CAMPAIGN_CREATE_SENTINEL"
    unsafe_inputs = (
        (
            (
                Participant(
                    "unsafe-participant",
                    "actor",
                    "synthetic.clockwork",
                    "1.0.0",
                    {"diagnostic_info": sentinel},
                ),
            ),
            (),
        ),
        ((), (StateValue("private_reasoning_payload", "value", sentinel),)),
        ((), (StateValue("clockwork", "tracebackText", sentinel),)),
        (
            (),
            (StateValue("clockwork", "value", {"inspector_trace_payload": sentinel}),),
        ),
    )

    for index, (participants, initial_state) in enumerate(unsafe_inputs):
        database = tmp_path / f"unsafe-create-{index}.db"
        owner = AuthorityOwner.open(database, owner_id="owner-alpha")
        campaign_id = f"unsafe-create-{index}"
        with pytest.raises(CommandRejected) as caught:
            owner.create_campaign(
                CampaignCreate(
                    display_name="Unsafe create probe",
                    writer=WriterRole.DM,
                    package_lock=_synthetic_lock(),
                    turn_id="turn-unsafe-create",
                    campaign_id=campaign_id,
                    participants=participants,
                    initial_state=initial_state,
                )
            )
        owner.close()

        with sqlite3.connect(database) as connection:
            campaign_rows = connection.execute(
                "SELECT COUNT(*) FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()[0]
            participant_rows = connection.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
            state_rows = connection.execute("SELECT COUNT(*) FROM campaign_state").fetchone()[0]
        print(
            "F1 UNSAFE CAMPAIGN CREATE REJECTED",
            index,
            caught.value.code,
            campaign_rows,
            participant_rows,
            state_rows,
        )
        assert caught.value.code == "unsafe_canonical_payload"
        assert campaign_rows == participant_rows == state_rows == 0
        assert sentinel.encode() not in database.read_bytes()

    neutral_database = tmp_path / "neutral-create.db"
    neutral_owner = AuthorityOwner.open(neutral_database, owner_id="owner-alpha")
    neutral_campaign = neutral_owner.create_campaign(
        CampaignCreate(
            display_name="Neutral create probe",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-neutral-create",
            participants=(
                Participant(
                    "neutral-participant",
                    "actor",
                    "synthetic.clockwork",
                    "1.0.0",
                    {"stack": ["one", "two"], "exception": "ordinary"},
                ),
            ),
            initial_state=(
                StateValue(
                    "stack",
                    "exception",
                    {"note": "private reasoning is safe prose under a neutral key"},
                ),
            ),
        )
    )
    neutral_snapshot = neutral_owner.snapshot(neutral_campaign.campaign_id)
    neutral_owner.close()
    print(
        "F1 NEUTRAL CAMPAIGN CREATE",
        neutral_snapshot.participants[0].data,
        neutral_snapshot.state,
    )
    assert neutral_snapshot.participants[0].data == {
        "stack": ["one", "two"],
        "exception": "ordinary",
    }
    assert neutral_snapshot.state == {
        "stack.exception": {"note": "private reasoning is safe prose under a neutral key"}
    }


def test_nested_object_and_array_reference_admission_is_recursive_and_schema_declared(
    tmp_path: Path,
) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    executions = 0

    def increment_nested(context: MechanicContext) -> MechanicOutcome:
        nonlocal executions
        executions += 1
        return _increment_counter(context)

    nested_reference_schema: Mapping[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["amount", "selection", "allies", "note"],
        "properties": {
            "amount": {"type": "integer", "minimum": 1},
            "selection": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target"],
                "properties": {
                    "target": {
                        "type": "string",
                        "x-sylvia-gm-reference": "participant",
                    }
                },
            },
            "allies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["participant"],
                    "properties": {
                        "participant": {
                            "type": "string",
                            "x-sylvia-gm-reference": "participant",
                        }
                    },
                },
            },
            "note": {"type": "string"},
        },
    }
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="nested_reference_probe",
            capability="synthetic.counter.increment",
            input_schema=nested_reference_schema,
            execute=increment_nested,
        )
    )
    first = owner.create_campaign(
        CampaignCreate(
            display_name="Nested reference campaign",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-nested-reference",
            participants=(
                Participant("first-a", "actor", "synthetic.clockwork", "1.0.0", {}),
                Participant("first-b", "actor", "synthetic.clockwork", "1.0.0", {}),
            ),
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    )
    owner.create_campaign(
        CampaignCreate(
            display_name="Foreign reference campaign",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-foreign-reference",
            participants=(Participant("foreign", "actor", "synthetic.clockwork", "1.0.0", {}),),
        )
    )
    base = replace(
        _counter_action(
            first.campaign_id,
            action_id="nested-reference",
            turn_id="turn-nested-reference",
        ),
        tool_name="nested_reference_probe",
    )

    with pytest.raises(CommandRejected):
        owner.execute_action(
            replace(
                base,
                action_id="missing-nested-required",
                idempotency_key="missing-nested-required",
                parameters={"amount": 1, "selection": {}, "allies": [], "note": "safe"},
            )
        )
    with pytest.raises(CommandRejected) as cross_campaign:
        owner.execute_action(
            replace(
                base,
                action_id="foreign-nested-reference",
                idempotency_key="foreign-nested-reference",
                parameters={
                    "amount": 1,
                    "selection": {"target": "first-a"},
                    "allies": [{"participant": "foreign"}],
                    "note": "safe",
                },
            )
        )
    before_valid = owner.snapshot(first.campaign_id)
    valid = owner.execute_action(
        replace(
            base,
            action_id="valid-nested-reference",
            idempotency_key="valid-nested-reference",
            parameters={
                "amount": 2,
                "selection": {"target": "first-a"},
                "allies": [{"participant": "first-b"}],
                "note": "foreign",
            },
        )
    )
    after_valid = owner.snapshot(first.campaign_id)
    owner.close()

    print(
        "F1 RECURSIVE REFERENCES",
        cross_campaign.value.code,
        executions,
        before_valid.revision,
        after_valid.revision,
        valid.result,
    )
    assert cross_campaign.value.code == "cross_campaign_reference"
    assert before_valid.revision == 0
    assert before_valid.events == ()
    assert executions == 1
    assert valid.status is ActionStatus.COMMITTED
    assert after_valid.revision == 1
    assert after_valid.state == {"clockwork.counter": 2}


def test_action_replay_durably_binds_each_new_transport_key(tmp_path: Path) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = _open_counter_owner(database, owner_id="owner-before-alias-reload")
    campaign = _create_counter_campaign(owner)
    original = _counter_action(
        campaign.campaign_id,
        action_id="alias-action-a",
        owner_id="owner-before-alias-reload",
        amount=3,
    )
    first = owner.execute_action(original)
    alias_key = "transport-alias-k2"
    alias_replay = owner.execute_action(replace(original, idempotency_key=alias_key))

    with pytest.raises(IdempotencyConflict) as before_reload_conflict:
        owner.execute_action(
            replace(
                original,
                action_id="alias-action-b",
                idempotency_key=alias_key,
                expected_revision=1,
                parameters={"amount": 1},
            )
        )
    before_reload = owner.snapshot(campaign.campaign_id)
    owner.close()

    fresh_owner = _open_counter_owner(database, owner_id="owner-after-alias-reload")
    durable_replay = fresh_owner.execute_action(
        replace(
            original,
            idempotency_key=alias_key,
            owner_id="owner-after-alias-reload",
        )
    )
    with pytest.raises(IdempotencyConflict) as after_reload_conflict:
        fresh_owner.execute_action(
            replace(
                original,
                action_id="alias-action-c",
                idempotency_key=alias_key,
                owner_id="owner-after-alias-reload",
                expected_revision=1,
                parameters={"amount": 2},
            )
        )
    after_reload = fresh_owner.snapshot(campaign.campaign_id)
    fresh_owner.close()

    print(
        "F1 DURABLE TRANSPORT ALIAS",
        first,
        alias_replay,
        durable_replay,
        before_reload_conflict.value.code,
        after_reload_conflict.value.code,
        after_reload.revision,
    )
    assert first == alias_replay == durable_replay
    assert before_reload_conflict.value.code == "idempotency_conflict"
    assert after_reload_conflict.value.code == "idempotency_conflict"
    assert before_reload == after_reload
    assert after_reload.revision == 1
    assert after_reload.state == {"clockwork.counter": 3}
    assert len(after_reload.actions) == 1
    assert len(after_reload.events) == 6


def test_package_lock_accepts_empty_closure_and_nonempty_still_requires_one_primary() -> None:
    empty_lock = PackageLock(entries=())
    dependency_only = PackageLockEntry(
        package_id="synthetic.foundation",
        version="1.0.0",
        sha256="b" * 64,
        role=PackageRole.DEPENDENCY,
        load_order=0,
        dependencies=(),
        capabilities=(),
    )

    with pytest.raises(ValueError, match="exactly one primary"):
        PackageLock(entries=(dependency_only,))

    print("F1 NO-RULESET LOCK", empty_lock, "memberships", len(empty_lock.entries))
    assert empty_lock.entries == ()


def test_no_rules_campaign_has_truthful_empty_closure_and_no_mechanics_surface(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-no-rules")
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Narrative only",
            writer=WriterRole.DM,
            package_lock=_no_rules_lock(),
            turn_id="turn-no-rules",
        )
    )
    lock = owner.current_package_lock(campaign.campaign_id)
    before = owner.snapshot(campaign.campaign_id)
    surfaces = owner.package_surfaces(campaign.campaign_id)

    with pytest.raises(CommandRejected) as rejected:
        owner.execute_action(
            ActionCommand(
                campaign_id=campaign.campaign_id,
                action_id="action-no-rules-mechanics",
                idempotency_key="idem-no-rules-mechanics",
                owner_id="owner-no-rules",
                writer=WriterRole.DM,
                expected_revision=0,
                turn_id="turn-no-rules",
                package_id="synthetic.clockwork",
                tool_name="increment_counter",
                parameters={"amount": 1},
            )
        )

    after = owner.snapshot(campaign.campaign_id)
    assert owner.catalog(campaign.campaign_id) == ()
    assert surfaces.prompt_fragment_ids == ()
    assert surfaces.ui_surface_ids == ()
    owner.close()

    with sqlite3.connect(database) as connection:
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "campaign_locks",
                "campaign_lock_memberships",
                "package_manifests",
                "package_blobs",
            )
        }

    print(
        "F1 NO-RULESET CAMPAIGN",
        campaign.campaign_id,
        "lock",
        lock.lock_id,
        lock.closure_sha256,
        "counts",
        counts,
        "mechanics",
        rejected.value.code,
    )
    assert lock.package_lock == _no_rules_lock()
    assert lock.availability.value == "available"
    assert before.package_lock == _no_rules_lock()
    assert before.participants == ()
    assert rejected.value.code == "tool_unavailable"
    assert after.revision == before.revision == 0
    assert after.events == before.events == ()
    assert after.actions == before.actions == ()
    assert counts == {
        "campaign_locks": 1,
        "campaign_lock_memberships": 0,
        "package_manifests": 0,
        "package_blobs": 0,
    }


def test_snapshot_is_one_coherent_sqlite_read_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    campaign = _create_counter_campaign(owner)
    campaign_read = threading.Event()
    release_snapshot = threading.Event()
    original_campaign_record = SQLiteAuthorityStore._campaign_record

    def pause_snapshot_after_campaign_read(row: sqlite3.Row) -> CampaignRecord:
        campaign_record = original_campaign_record(row)
        if threading.current_thread().name.startswith("coherent-snapshot-reader"):
            campaign_read.set()
            assert release_snapshot.wait(timeout=5)
        return campaign_record

    monkeypatch.setattr(
        SQLiteAuthorityStore,
        "_campaign_record",
        staticmethod(pause_snapshot_after_campaign_read),
    )
    with ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="coherent-snapshot-reader"
    ) as executor:
        pending_snapshot = executor.submit(owner.snapshot, campaign.campaign_id)
        assert campaign_read.wait(timeout=5)
        receipt = owner.execute_action(
            _counter_action(campaign.campaign_id, action_id="snapshot-interleave")
        )
        release_snapshot.set()
        interleaved = pending_snapshot.result(timeout=5)

    print(
        "F1 COHERENT SNAPSHOT",
        interleaved.revision,
        interleaved.state,
        len(interleaved.actions),
        len(interleaved.events),
        receipt.resulting_revision,
    )
    assert (
        interleaved.revision,
        interleaved.state,
        len(interleaved.actions),
        len(interleaved.events),
    ) in (
        (0, {"clockwork.counter": 0}, 0, 0),
        (1, {"clockwork.counter": 1}, 1, 6),
    )
    owner.close()


def test_catalog_executes_only_the_exact_locked_package_identity_and_reloads_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    v1_sha = "a" * 64
    v2_sha = "c" * 64
    v2_lock = PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.clockwork",
                version="2.0.0",
                sha256=v2_sha,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(),
                capabilities=("synthetic.counter.increment",),
            ),
        )
    )

    def implementation(
        version: str,
    ) -> Callable[[MechanicContext], MechanicOutcome]:
        def execute(_context: MechanicContext) -> MechanicOutcome:
            return MechanicOutcome(result={"implementation": version})

        return execute

    v1_tool = CatalogTool(
        package_id="synthetic.clockwork",
        package_version="1.0.0",
        package_sha256=v1_sha,
        name="increment_counter",
        capability="synthetic.counter.increment",
        input_schema=_counter_schema(),
        execute=implementation("v1"),
    )
    v2_tool = CatalogTool(
        package_id="synthetic.clockwork",
        package_version="2.0.0",
        package_sha256=v2_sha,
        name="increment_counter",
        capability="synthetic.counter.increment",
        input_schema=_counter_schema(),
        execute=implementation("v2"),
    )
    owner = AuthorityOwner.open(database, owner_id="owner-exact-before-reload")
    owner.register_tool(v1_tool)
    owner.register_tool(v2_tool)
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Exact runtime package binding",
            writer=WriterRole.DM,
            package_lock=v2_lock,
            turn_id="turn-exact-runtime",
            campaign_id="campaign-exact-runtime",
        )
    )
    admitted = owner.catalog(campaign.campaign_id)
    assert len(admitted) == 1
    actual_v2 = admitted[0]
    assert actual_v2.package_version == "2.0.0"
    actual_campaign_lock = owner.snapshot(campaign.campaign_id).package_lock
    assert actual_v2.package_sha256 == actual_campaign_lock.entries[0].sha256
    command = ActionCommand(
        campaign_id=campaign.campaign_id,
        action_id="exact-v2-runs",
        idempotency_key="exact-v2-runs",
        owner_id="owner-exact-before-reload",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="turn-exact-runtime",
        package_id="synthetic.clockwork",
        tool_name="increment_counter",
        parameters={"amount": 1},
    )
    receipt = owner.execute_action(command)
    before_reload = owner.snapshot(campaign.campaign_id)
    owner.close()

    fresh_owner = AuthorityOwner.open(database, owner_id="owner-exact-after-reload")
    fresh_owner.register_tool(
        replace(v1_tool, package_sha256=before_reload.package_lock.entries[0].sha256)
    )
    fresh_owner.register_tool(
        replace(v2_tool, package_sha256=before_reload.package_lock.entries[0].sha256)
    )
    reloaded = fresh_owner.snapshot(campaign.campaign_id)
    reloaded_catalog = fresh_owner.catalog(campaign.campaign_id)
    fresh_owner.close()

    print(
        "F1 EXACT PACKAGE RUNTIME BINDING",
        receipt.result,
        reloaded.package_lock,
        [(tool.package_version, tool.package_sha256) for tool in reloaded_catalog],
    )
    assert receipt.result == {"implementation": "v2"}
    assert before_reload.package_lock == reloaded.package_lock
    assert before_reload.package_lock != v2_lock
    assert len(reloaded_catalog) == 1
    assert reloaded_catalog[0].package_version == "2.0.0"


def test_package_lock_rejects_more_than_one_identity_for_one_logical_package() -> None:
    primary = PackageLockEntry(
        package_id="synthetic.clockwork",
        version="1.0.0",
        sha256="a" * 64,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(),
        capabilities=("synthetic.counter.increment",),
    )
    ambiguous_identities = (
        replace(
            primary,
            sha256="b" * 64,
            role=PackageRole.DEPENDENCY,
            load_order=1,
        ),
        replace(
            primary,
            version="2.0.0",
            sha256="c" * 64,
            role=PackageRole.DEPENDENCY,
            load_order=1,
        ),
    )

    for ambiguous in ambiguous_identities:
        with pytest.raises(ValueError, match="logical package"):
            PackageLock(entries=(primary, ambiguous))


def test_package_lock_rejects_duplicate_dependency_membership() -> None:
    dependency_entry = PackageLockEntry(
        package_id="synthetic.foundation",
        version="1.0.0",
        sha256="b" * 64,
        role=PackageRole.DEPENDENCY,
        load_order=1,
        dependencies=(),
        capabilities=(),
    )
    dependency = PackageDependency(
        package_id=dependency_entry.package_id,
        version=dependency_entry.version,
        sha256=dependency_entry.sha256,
        kind="library",
    )
    with pytest.raises(ValueError, match="dependency membership"):
        primary = PackageLockEntry(
            package_id="synthetic.clockwork",
            version="1.0.0",
            sha256="a" * 64,
            role=PackageRole.PRIMARY,
            load_order=0,
            dependencies=(dependency, dependency),
            capabilities=("synthetic.counter.increment",),
        )
        PackageLock(entries=(primary, dependency_entry))


def test_exact_dependency_order_round_trips_deterministically(tmp_path: Path) -> None:
    zeta = PackageLockEntry(
        package_id="synthetic.zeta",
        version="1.0.0",
        sha256="d" * 64,
        role=PackageRole.DEPENDENCY,
        load_order=1,
        dependencies=(),
        capabilities=(),
    )
    alpha = PackageLockEntry(
        package_id="synthetic.alpha",
        version="1.0.0",
        sha256="e" * 64,
        role=PackageRole.DEPENDENCY,
        load_order=2,
        dependencies=(),
        capabilities=(),
    )
    ordered_lock = PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.clockwork",
                version="1.0.0",
                sha256="a" * 64,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(
                    PackageDependency(
                        zeta.package_id,
                        zeta.version,
                        zeta.sha256,
                        "zeta-library",
                        "zeta",
                    ),
                    PackageDependency(
                        alpha.package_id,
                        alpha.version,
                        alpha.sha256,
                        "alpha-library",
                        "alpha",
                    ),
                ),
                capabilities=("synthetic.counter.increment",),
            ),
            zeta,
            alpha,
        )
    )
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-dependency-order")
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Ordered dependency closure",
            writer=WriterRole.DM,
            package_lock=ordered_lock,
            turn_id="turn-dependency-order",
        )
    )
    before_reload = owner.snapshot(campaign.campaign_id)
    owner.close()
    fresh_owner = AuthorityOwner.open(database, owner_id="owner-dependency-order-reload")
    reloaded = fresh_owner.snapshot(campaign.campaign_id)
    fresh_owner.close()

    print(
        "F1 ORDERED EXACT DEPENDENCIES",
        [dependency.package_id for dependency in reloaded.package_lock.entries[0].dependencies],
    )
    assert before_reload.package_lock == reloaded.package_lock
    assert before_reload.package_lock != ordered_lock
    assert [
        dependency.namespace for dependency in reloaded.package_lock.entries[0].dependencies
    ] == ["zeta", "alpha"]


def test_close_quiesces_admitted_action_before_releasing_owner_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = _open_counter_owner(database, owner_id="owner-before-close")
    campaign = _create_counter_campaign(owner)
    action_at_precommit = threading.Event()
    release_action = threading.Event()
    close_started = threading.Event()
    close_returned = threading.Event()

    def pause_before_commit(_store: SQLiteAuthorityStore) -> None:
        action_at_precommit.set()
        assert release_action.wait(timeout=5)

    monkeypatch.setattr(SQLiteAuthorityStore, "_before_commit", pause_before_commit)
    command = _counter_action(
        campaign.campaign_id,
        action_id="commit-before-close-returns",
        owner_id="owner-before-close",
    )

    def close_owner() -> None:
        close_started.set()
        owner.close()
        close_returned.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        action_future = executor.submit(owner.execute_action, command)
        assert action_at_precommit.wait(timeout=5)
        close_future = executor.submit(close_owner)
        assert close_started.wait(timeout=5)
        try:
            assert not close_returned.wait(timeout=0.25)
            with pytest.raises(OwnerConflict):
                AuthorityOwner.open(database, owner_id="owner-too-early")
        finally:
            release_action.set()
        receipt = action_future.result(timeout=5)
        close_future.result(timeout=5)

    fresh_owner = _open_counter_owner(database, owner_id="owner-after-close")
    committed = fresh_owner.snapshot(campaign.campaign_id)
    fresh_owner.close()
    owner.close()

    with pytest.raises(RuntimeError, match="closed"):
        owner.snapshot(campaign.campaign_id)
    with pytest.raises(RuntimeError, match="closed"):
        owner.execute_action(
            replace(command, action_id="after-close", idempotency_key="after-close")
        )

    print(
        "F1 CLOSE QUIESCENCE",
        receipt.resulting_revision,
        close_returned.is_set(),
        committed.revision,
        committed.state,
    )
    assert receipt.resulting_revision == 1
    assert close_returned.is_set()
    assert committed.revision == 1
    assert committed.state == {"clockwork.counter": 1}
    assert len(committed.actions) == 1


def test_open_rejects_user_version_one_database_without_f1_schema(tmp_path: Path) -> None:
    database = tmp_path / "sylvia-gm.db"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 1")

    def open_and_close_if_incorrectly_admitted() -> None:
        owner = AuthorityOwner.open(database, owner_id="owner-invalid-schema")
        owner.close()

    with pytest.raises(AuthorityError) as caught:
        open_and_close_if_incorrectly_admitted()

    print("F1 MISSING SCHEMA REJECTED", type(caught.value).__name__, caught.value)
    assert type(caught.value).__name__ == "SchemaIdentityError"
    assert getattr(caught.value, "code", None) == "schema_identity_mismatch"
    assert "no such table" not in str(caught.value).casefold()


def test_migration_identity_is_deterministic_and_fail_closed(tmp_path: Path) -> None:
    observed_identity: tuple[tuple[int, str, str], ...] = ()
    corruptions = (
        ("missing-1", 1, "missing"),
        ("checksum-1", 1, "checksum"),
        ("missing-2", 2, "missing"),
        ("checksum-2", 2, "checksum"),
        ("missing-3", 3, "missing"),
        ("checksum-3", 3, "checksum"),
    )
    for corruption, target_version, kind in corruptions:
        database = tmp_path / f"migration-{corruption}.db"
        owner = AuthorityOwner.open(database, owner_id=f"owner-valid-{corruption}")
        owner.close()
        reopened = AuthorityOwner.open(database, owner_id=f"owner-reopened-{corruption}")
        reopened.close()

        with sqlite3.connect(database) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            assert version == 3
            columns = tuple(
                str(row[1]) for row in connection.execute("PRAGMA table_info(schema_migrations)")
            )
            assert columns == ("version", "applied_name", "sql_sha256")
            rows = tuple(
                (int(row[0]), str(row[1]), str(row[2]))
                for row in connection.execute(
                    "SELECT version, applied_name, sql_sha256 "
                    "FROM schema_migrations ORDER BY version"
                )
            )
            assert len(rows) == 3
            observed_identity = rows
            assert tuple(row[:2] for row in rows) == (
                (1, "f1_neutral_authority"),
                (2, "f2_provisional_event_protocol"),
                (3, "f3_sqlite_package_library"),
            )
            assert all(len(row[2]) == 64 for row in rows)
            assert all(set(row[2]) <= set("0123456789abcdef") for row in rows)
            if kind == "missing":
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version = ?", (target_version,)
                )
            else:
                connection.execute(
                    "UPDATE schema_migrations SET sql_sha256 = ? WHERE version = ?",
                    ("f" * 64, target_version),
                )

        with pytest.raises(AuthorityError) as caught:
            AuthorityOwner.open(database, owner_id=f"owner-corrupt-{corruption}")
        assert type(caught.value).__name__ == "SchemaIdentityError"
        assert getattr(caught.value, "code", None) == "schema_identity_mismatch"

    print("F1 DETERMINISTIC MIGRATION IDENTITIES", observed_identity)


def test_open_rejects_constraint_corruption_with_required_table_names_intact(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-before-schema-corruption")
    owner.close()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE campaign_package_memberships")
        connection.execute(
            "CREATE TABLE campaign_package_memberships ("
            "campaign_id TEXT NOT NULL, package_id TEXT NOT NULL, "
            "version TEXT NOT NULL, sha256 TEXT NOT NULL, "
            "role TEXT NOT NULL, load_order INTEGER NOT NULL)"
        )

    def open_and_close_if_incorrectly_admitted() -> None:
        corrupted_owner = AuthorityOwner.open(database, owner_id="owner-after-schema-corruption")
        corrupted_owner.close()

    with pytest.raises(AuthorityError) as caught:
        open_and_close_if_incorrectly_admitted()

    print("F1 CONSTRAINT IDENTITY REJECTED", type(caught.value).__name__)
    assert type(caught.value).__name__ == "SchemaIdentityError"
    assert getattr(caught.value, "code", None) == "schema_identity_mismatch"


def test_mechanic_outcome_is_captured_once_and_all_return_projections_are_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-alpha")
    retained_result: dict[str, JsonValue] = {"values": [1]}
    retained_mutation: dict[str, JsonValue] = {"values": [2]}
    retained_consequence: dict[str, JsonValue] = {"values": [3]}
    sentinel = "ALTERNATING_OUTCOME_SENTINEL"

    class AlternatingResult(Mapping[str, JsonValue]):
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            self.iterations += 1
            if self.iterations <= 2:
                return iter(("status", "payload"))
            return iter(("inspectortracepayload",))

        def __len__(self) -> int:
            return 2

        def __getitem__(self, key: str) -> JsonValue:
            if key == "status":
                return "committed"
            if key == "payload":
                return retained_result
            if key == "inspectortracepayload":
                return sentinel
            raise KeyError(key)

    alternating = AlternatingResult()

    def return_retained_aliases(_context: MechanicContext) -> MechanicOutcome:
        return MechanicOutcome(
            result=alternating,
            mutations=(StateMutation("clockwork", "owned", retained_mutation),),
            consequences=({"kind": "owned_consequence", "payload": retained_consequence},),
        )

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="owned_outcome_probe",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
            execute=return_retained_aliases,
        )
    )
    campaign = _create_counter_campaign(owner)

    application_check = application_authority.mechanic_outcome_is_safe
    persistence_check = sqlite_authority.mechanic_outcome_is_safe

    def mutate_after_application_validation(outcome: MechanicOutcome) -> bool:
        checked = application_check(outcome)
        retained_result["values"] = [10]
        retained_mutation["values"] = [20]
        retained_consequence["values"] = [30]
        return checked

    def mutate_after_persistence_validation(outcome: MechanicOutcome) -> bool:
        checked = persistence_check(outcome)
        retained_result["values"] = [100]
        retained_mutation["values"] = [200]
        retained_consequence["values"] = [300]
        return checked

    monkeypatch.setattr(
        application_authority,
        "mechanic_outcome_is_safe",
        mutate_after_application_validation,
    )
    monkeypatch.setattr(
        sqlite_authority,
        "mechanic_outcome_is_safe",
        mutate_after_persistence_validation,
    )
    command = replace(
        _counter_action(campaign.campaign_id, action_id="owned-outcome"),
        tool_name="owned_outcome_probe",
        parameters={},
    )

    receipt = owner.execute_action(command)
    retained_result["values"] = [1000]
    retained_mutation["values"] = [2000]
    retained_consequence["values"] = [3000]
    snapshot = owner.snapshot(campaign.campaign_id)
    replay = owner.execute_action(replace(command, idempotency_key="owned-outcome-replay"))

    def assert_genuinely_immutable(value: object) -> None:
        if isinstance(value, Mapping):
            assert not isinstance(value, dict)
            with pytest.raises(TypeError):
                dict.__setitem__(cast(dict[str, object], value), "forged", True)
            for nested in value.values():
                assert_genuinely_immutable(nested)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            assert not isinstance(value, list)
            with pytest.raises(TypeError):
                list.append(cast(list[object], value), "forged")
            for nested in value:
                assert_genuinely_immutable(nested)

    assert_genuinely_immutable(receipt.result)
    assert_genuinely_immutable(snapshot.state)
    assert_genuinely_immutable(snapshot.turns)
    for participant in snapshot.participants:
        assert_genuinely_immutable(participant.data)
    for action in snapshot.actions:
        assert_genuinely_immutable(action.result)
    for event in snapshot.events:
        assert_genuinely_immutable(event.safe_payload)
    for mutation in snapshot.mutations:
        assert_genuinely_immutable(mutation.before)
        assert_genuinely_immutable(mutation.after)
    for consequence in snapshot.consequences:
        assert_genuinely_immutable(consequence.payload)

    result_payload = receipt.result["payload"]
    assert isinstance(result_payload, Mapping)
    result_values = result_payload["values"]
    assert isinstance(result_values, Sequence)
    with pytest.raises(TypeError):
        cast(FrozenJsonList, result_values).append(999)
    with pytest.raises(TypeError):
        list.append(cast(list[JsonValue], result_values), 999)
    with pytest.raises(TypeError):
        dict.__setitem__(cast(dict[str, JsonValue], receipt.result), "forged", True)
    state_payload = snapshot.state["clockwork.owned"]
    assert isinstance(state_payload, Mapping)
    with pytest.raises(TypeError):
        state_payload["values"] = [999]  # type: ignore[index]
    tool_result_payload = snapshot.events[2].safe_payload["result"]
    assert isinstance(tool_result_payload, Mapping)
    with pytest.raises(TypeError):
        tool_result_payload["changed"] = True  # type: ignore[index]

    owner.close()
    fresh_owner = AuthorityOwner.open(database, owner_id="owner-reloaded")
    reloaded = fresh_owner.snapshot(campaign.campaign_id)
    durable_replay = fresh_owner.execute_action(replace(command, owner_id="owner-reloaded"))
    fresh_owner.close()

    print(
        "F1 OWNED IMMUTABLE OUTCOME",
        alternating.iterations,
        receipt.result,
        snapshot.state,
        snapshot.consequences[0].payload,
        replay == durable_replay == receipt,
    )
    assert receipt.status is ActionStatus.COMMITTED
    assert receipt.result == {"status": "committed", "payload": {"values": [1]}}
    assert snapshot.state["clockwork.owned"] == {"values": [2]}
    assert snapshot.consequences[0].payload == {
        "kind": "owned_consequence",
        "payload": {"values": [3]},
    }
    assert replay == durable_replay == receipt
    assert reloaded == snapshot
    assert json.loads(json.dumps(thaw_json_value(receipt.result))) == thaw_json_value(
        receipt.result
    )
    assert alternating.iterations == 1
    assert sentinel.encode() not in database.read_bytes()


def test_additional_properties_reference_schemas_follow_validation_recursively(
    tmp_path: Path,
) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    executions = 0

    def execute_dynamic_reference(context: MechanicContext) -> MechanicOutcome:
        nonlocal executions
        executions += 1
        return _increment_counter(context)

    schema: Mapping[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["amount", "targets", "groups"],
        "properties": {
            "amount": {"type": "integer", "minimum": 1},
            "targets": {
                "type": "object",
                "properties": {},
                "additionalProperties": {
                    "type": "string",
                    "x-sylvia-gm-reference": "participant",
                },
            },
            "groups": {
                "type": "object",
                "properties": {},
                "additionalProperties": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["participant"],
                        "properties": {
                            "participant": {
                                "type": "string",
                                "x-sylvia-gm-reference": "participant",
                            }
                        },
                    },
                },
            },
        },
    }
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="dynamic_reference_probe",
            capability="synthetic.counter.increment",
            input_schema=schema,
            execute=execute_dynamic_reference,
        )
    )
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Dynamic reference campaign",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-dynamic-reference",
            participants=(
                Participant("local-a", "actor", "synthetic.clockwork", "1.0.0", {}),
                Participant("local-b", "actor", "synthetic.clockwork", "1.0.0", {}),
            ),
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    )
    owner.create_campaign(
        CampaignCreate(
            display_name="Foreign dynamic reference campaign",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-foreign-dynamic",
            participants=(Participant("foreign", "actor", "synthetic.clockwork", "1.0.0", {}),),
        )
    )
    base = replace(
        _counter_action(
            campaign.campaign_id,
            action_id="dynamic-reference",
            turn_id="turn-dynamic-reference",
        ),
        tool_name="dynamic_reference_probe",
    )

    with pytest.raises(CommandRejected) as direct_foreign:
        owner.execute_action(
            replace(
                base,
                action_id="dynamic-direct-foreign",
                idempotency_key="dynamic-direct-foreign",
                parameters={"amount": 1, "targets": {"lead": "foreign"}, "groups": {}},
            )
        )
    with pytest.raises(CommandRejected) as nested_foreign:
        owner.execute_action(
            replace(
                base,
                action_id="dynamic-nested-foreign",
                idempotency_key="dynamic-nested-foreign",
                parameters={
                    "amount": 1,
                    "targets": {"lead": "local-a"},
                    "groups": {"support": [{"participant": "foreign"}]},
                },
            )
        )
    before_valid = owner.snapshot(campaign.campaign_id)
    valid = owner.execute_action(
        replace(
            base,
            action_id="dynamic-valid",
            idempotency_key="dynamic-valid",
            parameters={
                "amount": 2,
                "targets": {"lead": "local-a"},
                "groups": {"support": [{"participant": "local-b"}]},
            },
        )
    )
    after_valid = owner.snapshot(campaign.campaign_id)
    owner.close()

    print(
        "F1 ADDITIONAL PROPERTIES REFERENCES",
        direct_foreign.value.code,
        nested_foreign.value.code,
        executions,
        before_valid.revision,
        after_valid.revision,
    )
    assert direct_foreign.value.code == nested_foreign.value.code == "cross_campaign_reference"
    assert executions == 1
    assert before_valid.revision == 0
    assert before_valid.events == ()
    assert valid.status is ActionStatus.COMMITTED
    assert after_valid.revision == 1
    assert after_valid.state == {"clockwork.counter": 2}


def test_package_lock_rejects_unsorted_entry_representation_before_persistence() -> None:
    primary = PackageLockEntry(
        package_id="synthetic.clockwork",
        version="1.0.0",
        sha256=SYNTHETIC_SHA,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(),
        capabilities=("synthetic.counter.increment",),
    )
    dependency = PackageLockEntry(
        package_id="synthetic.foundation",
        version="1.0.0",
        sha256="b" * 64,
        role=PackageRole.DEPENDENCY,
        load_order=1,
        dependencies=(),
        capabilities=(),
    )

    with pytest.raises(ValueError, match="canonical load order") as caught:
        PackageLock(entries=(dependency, primary))

    canonical = PackageLock(entries=(primary, dependency))
    print(
        "F1 EXACT LOCK REPRESENTATION",
        str(caught.value),
        [entry.load_order for entry in canonical.entries],
    )
    assert canonical.entries == (primary, dependency)


def test_owner_startup_rejects_corrupt_foreign_keys_and_incomplete_lock_closure(
    tmp_path: Path,
) -> None:
    dependency = PackageLockEntry(
        package_id="synthetic.foundation",
        version="1.0.0",
        sha256="b" * 64,
        role=PackageRole.DEPENDENCY,
        load_order=1,
        dependencies=(),
        capabilities=(),
    )
    primary = PackageLockEntry(
        package_id="synthetic.clockwork",
        version="1.0.0",
        sha256=SYNTHETIC_SHA,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=(
            PackageDependency(
                dependency.package_id,
                dependency.version,
                dependency.sha256,
                "library",
            ),
        ),
        capabilities=("synthetic.counter.increment",),
    )
    exact_lock = PackageLock(entries=(primary, dependency))

    failures: list[tuple[str, SchemaIdentityError]] = []
    for corruption in ("foreign-key", "incomplete-closure"):
        database = tmp_path / f"startup-{corruption}.db"
        owner = AuthorityOwner.open(database, owner_id=f"owner-before-{corruption}")
        campaign = owner.create_campaign(
            CampaignCreate(
                display_name="Startup integrity probe",
                writer=WriterRole.DM,
                package_lock=exact_lock,
                turn_id="turn-startup-integrity",
                campaign_id=f"campaign-{corruption}",
            )
        )
        actual_lock = owner.snapshot(campaign.campaign_id).package_lock
        actual_dependency = actual_lock.entries[1]
        owner.close()

        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            if corruption == "foreign-key":
                connection.execute(
                    "DELETE FROM packages WHERE package_id = ? AND version = ? AND sha256 = ?",
                    (
                        actual_dependency.package_id,
                        actual_dependency.version,
                        actual_dependency.sha256,
                    ),
                )
            else:
                current_lock_id = str(
                    connection.execute(
                        "SELECT current_lock_id FROM campaigns WHERE campaign_id = ?",
                        (campaign.campaign_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    "DELETE FROM campaign_lock_memberships WHERE lock_id = ? AND package_id = ?",
                    (current_lock_id, actual_dependency.package_id),
                )
                connection.execute(
                    "UPDATE campaign_locks SET closure_sha256 = ? WHERE lock_id = ?",
                    ("0" * 64, current_lock_id),
                )

        with pytest.raises(SchemaIdentityError) as caught:
            AuthorityOwner.open(database, owner_id=f"owner-after-{corruption}")
        failures.append((corruption, caught.value))

    print(
        "F1 STARTUP STORED AUTHORITY INTEGRITY",
        [(name, type(error).__name__, error.code) for name, error in failures],
    )
    assert [type(error).__name__ for _, error in failures] == [
        "SchemaIdentityError",
        "SchemaIdentityError",
    ]
    assert all(error.code == "schema_identity_mismatch" for _, error in failures)
    assert all("foreign key" not in str(error).casefold() for _, error in failures)
    assert all("synthetic" not in str(error).casefold() for _, error in failures)


def test_all_malformed_mechanic_internals_collapse_to_safe_failed_chronology(
    tmp_path: Path,
) -> None:
    class ExplodingItems(dict[str, JsonValue]):
        def items(self) -> Never:
            raise RuntimeError("RAW_ITERATOR_DIAGNOSTIC")

    outcomes: tuple[tuple[str, MechanicOutcome], ...] = (
        (
            "top-level-result",
            MechanicOutcome(result=cast(Mapping[str, JsonValue], ["not-an-object"])),
        ),
        (
            "mutation-namespace",
            MechanicOutcome(
                result={"status": "committed"},
                mutations=(StateMutation(cast(str, 7), "key", 1),),
            ),
        ),
        (
            "mutation-key",
            MechanicOutcome(
                result={"status": "committed"},
                mutations=(StateMutation("namespace", cast(str, 7), 1),),
            ),
        ),
        (
            "mutation-value",
            MechanicOutcome(
                result={"status": "committed"},
                mutations=(StateMutation("namespace", "key", cast(JsonValue, object())),),
            ),
        ),
        (
            "consequence-shape",
            MechanicOutcome(
                result={"status": "committed"},
                consequences=cast(tuple[Mapping[str, JsonValue], ...], (object(),)),
            ),
        ),
        (
            "consequence-kind-type",
            MechanicOutcome(result={"status": "committed"}, consequences=({"kind": 7},)),
        ),
        (
            "consequence-kind-empty",
            MechanicOutcome(result={"status": "committed"}, consequences=({"kind": ""},)),
        ),
        (
            "consequence-payload",
            MechanicOutcome(
                result={"status": "committed"},
                consequences=({"kind": "invalid", "payload": cast(JsonValue, object())},),
            ),
        ),
        (
            "iterator-error",
            MechanicOutcome(result=ExplodingItems({"status": "committed"})),
        ),
    )

    observed: list[tuple[str, ActionStatus, int, tuple[CanonicalEventType, ...]]] = []
    for case_name, outcome in outcomes:
        database = tmp_path / f"malformed-{case_name}.db"
        owner = _open_counter_owner(database, owner_id=f"owner-{case_name}")

        def return_selected(
            _context: MechanicContext, selected: MechanicOutcome = outcome
        ) -> MechanicOutcome:
            return selected

        owner.register_tool(
            CatalogTool(
                package_id="synthetic.clockwork",
                package_version="1.0.0",
                package_sha256=SYNTHETIC_SHA,
                name=f"malformed_{case_name}",
                capability="synthetic.counter.increment",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["amount"],
                    "properties": {"amount": {"type": "integer", "minimum": 1}},
                },
                execute=return_selected,
            )
        )
        campaign = owner.create_campaign(
            CampaignCreate(
                display_name=f"Malformed {case_name}",
                writer=WriterRole.DM,
                package_lock=_synthetic_lock(),
                turn_id=f"turn-{case_name}",
                initial_state=(StateValue("clockwork", "counter", 0),),
            )
        )
        receipt = owner.execute_action(
            replace(
                _counter_action(
                    campaign.campaign_id,
                    action_id=f"malformed-{case_name}",
                    turn_id=f"turn-{case_name}",
                ),
                owner_id=f"owner-{case_name}",
                tool_name=f"malformed_{case_name}",
            )
        )
        snapshot = owner.snapshot(campaign.campaign_id)
        owner.close()
        observed.append(
            (
                case_name,
                receipt.status,
                snapshot.revision,
                tuple(event.event_type for event in snapshot.events),
            )
        )
        assert receipt.result == {
            "status": "failed",
            "code": "tool_execution_failed",
        }
        assert snapshot.state == {"clockwork.counter": 0}
        assert b"RAW_ITERATOR_DIAGNOSTIC" not in database.read_bytes()

    print("F1 MALFORMED INTERNALS", observed)
    assert all(status is ActionStatus.FAILED for _, status, _, _ in observed)
    assert all(revision == 1 for _, _, revision, _ in observed)
    assert all(
        event_types
        == (
            CanonicalEventType.ACTION_DECLARED,
            CanonicalEventType.TOOL_CALL,
            CanonicalEventType.TOOL_RESULT,
            CanonicalEventType.ACTION_FAILED,
        )
        for _, _, _, event_types in observed
    )


def test_registered_tool_schema_is_owned_before_reference_admission(tmp_path: Path) -> None:
    owner = _open_counter_owner(tmp_path / "sylvia-gm.db")
    executions = 0
    target_schema: dict[str, object] = {
        "type": "string",
        "x-sylvia-gm-reference": "participant",
    }
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["target", "amount"],
        "properties": {
            "target": target_schema,
            "amount": {"type": "integer", "minimum": 1},
        },
    }

    class CountingSchema(Mapping[str, object]):
        iterations = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            self.iterations += 1
            return iter(schema)

        def __len__(self) -> int:
            return len(schema)

        def __getitem__(self, key: str) -> object:
            return schema[key]

    counting_schema = CountingSchema()

    def execute_owned_schema(context: MechanicContext) -> MechanicOutcome:
        nonlocal executions
        executions += 1
        return _increment_counter(context)

    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="owned_schema_probe",
            capability="synthetic.counter.increment",
            input_schema=counting_schema,
            execute=execute_owned_schema,
        )
    )
    local = owner.create_campaign(
        CampaignCreate(
            display_name="Owned schema local",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-owned-schema-local",
            participants=(Participant("local", "actor", "synthetic.clockwork", "1.0.0", {}),),
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    )
    owner.create_campaign(
        CampaignCreate(
            display_name="Owned schema foreign",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-owned-schema-foreign",
            participants=(Participant("foreign", "actor", "synthetic.clockwork", "1.0.0", {}),),
        )
    )

    target_schema.pop("x-sylvia-gm-reference")
    cast(dict[str, object], schema["properties"])["target"] = {"type": "string"}

    foreign = ActionCommand(
        campaign_id=local.campaign_id,
        action_id="owned-schema-foreign",
        idempotency_key="owned-schema-foreign",
        owner_id="owner-alpha",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="turn-owned-schema-local",
        package_id="synthetic.clockwork",
        tool_name="owned_schema_probe",
        parameters={"target": "foreign", "amount": 1},
    )
    with pytest.raises(CommandRejected) as caught:
        owner.execute_action(foreign)
    same_campaign = owner.execute_action(
        replace(
            foreign,
            action_id="owned-schema-local",
            idempotency_key="owned-schema-local",
            parameters={"target": "local", "amount": 1},
        )
    )
    snapshot = owner.snapshot(local.campaign_id)
    owner.close()

    print(
        "F1 OWNED REGISTERED SCHEMA",
        counting_schema.iterations,
        caught.value.code,
        executions,
        same_campaign.status.value,
        snapshot.revision,
    )
    assert counting_schema.iterations == 1
    assert caught.value.code == "cross_campaign_reference"
    assert executions == 1
    assert same_campaign.status is ActionStatus.COMMITTED
    assert snapshot.revision == 1


def test_campaign_creation_owns_collections_once_before_validation_and_reload(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-alpha")
    retained_data: dict[str, JsonValue] = {"labels": ["owned"]}
    retained_state: list[JsonValue] = [1]
    locked = Participant(
        "locked",
        "actor",
        "synthetic.clockwork",
        "1.0.0",
        retained_data,
    )
    unlocked = Participant("foreign", "actor", "not.locked", "9.9.9", {})

    class ChangingParticipants(tuple[Participant, ...]):
        iterations = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            self.iterations += 1
            return iter((locked,)) if self.iterations == 1 else iter((unlocked,))

    changing = ChangingParticipants((locked, unlocked))
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Owned campaign creation",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-owned-creation",
            participants=changing,
            initial_state=(StateValue("clockwork", "owned", retained_state),),
        )
    )
    retained_data["labels"] = ["mutated"]
    retained_state.append(999)
    created = owner.snapshot(campaign.campaign_id)
    owner.close()

    reopened = AuthorityOwner.open(database, owner_id="owner-reopened")
    reloaded = reopened.snapshot(campaign.campaign_id)
    reopened.close()

    print(
        "F1 OWNED CAMPAIGN CREATION",
        changing.iterations,
        [
            (item.participant_id, item.package_id, item.package_version)
            for item in reloaded.participants
        ],
        reloaded.participants[0].data,
        reloaded.state,
    )
    assert changing.iterations == 1
    assert created == reloaded
    assert [
        (item.participant_id, item.package_id, item.package_version)
        for item in reloaded.participants
    ] == [("locked", "synthetic.clockwork", "1.0.0")]
    assert reloaded.participants[0].data == {"labels": ["owned"]}
    assert reloaded.state == {"clockwork.owned": [1]}


def test_store_rejects_unlocked_participant_package_atomically(tmp_path: Path) -> None:
    database = tmp_path / "sylvia-gm.db"
    owner = AuthorityOwner.open(database, owner_id="owner-alpha")
    store = owner._store
    campaign = CampaignRecord(
        campaign_id="store-defense-campaign",
        display_name="Store defense",
        lifecycle=LifecycleState.ACTIVE,
        writer=WriterRole.DM,
        revision=0,
    )

    with pytest.raises(CommandRejected) as caught:
        store.create_campaign(
            campaign,
            _synthetic_lock(),
            "turn-store-defense",
            (
                Participant(
                    "foreign",
                    "actor",
                    "not.locked",
                    "9.9.9",
                    {"safe": True},
                ),
            ),
            (StateValue("clockwork", "counter", 0),),
        )
    owner.close()

    with sqlite3.connect(database) as connection:
        row_counts = dict(
            connection.execute(
                "SELECT 'campaigns', COUNT(*) FROM campaigns UNION ALL "
                "SELECT 'campaign_package_memberships', COUNT(*) "
                "FROM campaign_package_memberships UNION ALL "
                "SELECT 'participants', COUNT(*) FROM participants UNION ALL "
                "SELECT 'campaign_state', COUNT(*) FROM campaign_state UNION ALL "
                "SELECT 'turns', COUNT(*) FROM turns UNION ALL "
                "SELECT 'campaign_revisions', COUNT(*) FROM campaign_revisions"
            )
        )

    print("F1 STORE PARTICIPANT LOCK DEFENSE", caught.value.code, row_counts)
    assert caught.value.code == "participant_package_unlocked"
    assert set(row_counts.values()) == {0}


def test_package_lock_owns_exact_tuple_and_dependency_representation() -> None:
    dependency = PackageDependency(
        package_id="synthetic.foundation",
        version="1.0.0",
        sha256="b" * 64,
        kind="library",
    )

    class CountingDependencies(tuple[PackageDependency, ...]):
        iterations = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            self.iterations += 1
            return super().__iter__()

    class CountingCapabilities(tuple[str, ...]):
        iterations = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            self.iterations += 1
            return super().__iter__()

    dependencies = CountingDependencies((dependency,))
    capabilities = CountingCapabilities(("synthetic.counter.increment",))
    primary = PackageLockEntry(
        package_id="synthetic.clockwork",
        version="1.0.0",
        sha256=SYNTHETIC_SHA,
        role=PackageRole.PRIMARY,
        load_order=0,
        dependencies=dependencies,
        capabilities=capabilities,
    )
    foundation = PackageLockEntry(
        package_id="synthetic.foundation",
        version="1.0.0",
        sha256="b" * 64,
        role=PackageRole.DEPENDENCY,
        load_order=1,
        dependencies=(),
        capabilities=(),
    )

    class CountingEntries(tuple[PackageLockEntry, ...]):
        iterations = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            self.iterations += 1
            return super().__iter__()

    entries = CountingEntries((primary, foundation))
    package_lock = PackageLock(entries=entries)

    print(
        "F1 OWNED PACKAGE LOCK",
        entries.iterations,
        dependencies.iterations,
        capabilities.iterations,
        package_lock,
    )
    assert entries.iterations == 1
    assert dependencies.iterations == 1
    assert capabilities.iterations == 1
    assert type(package_lock.entries) is tuple
    assert type(package_lock.entries[0].dependencies) is tuple
    assert type(package_lock.entries[0].capabilities) is tuple


def test_private_channel_families_are_order_independent_and_never_persist(
    tmp_path: Path,
) -> None:
    sentinel = "ORDER_INDEPENDENT_PRIVATE_SENTINEL"
    reserved_keys = (
        "traceinspectorpayload",
        "traceprivatepayload",
        "inspectortracepayload",
        "privatetracepayload",
        "trace_inspector_payload",
        "tracePrivatePayload",
        "reasoningprivatepayload",
        "rawReasoningPayload",
        "payloadDiagnosticInfo",
        "payload_traceback_text",
    )
    observed: list[tuple[str, ActionStatus, tuple[CanonicalEventType, ...]]] = []

    for index, reserved_key in enumerate(reserved_keys):
        database = tmp_path / f"private-family-{index}.db"
        owner = _open_counter_owner(database, owner_id=f"owner-private-{index}")

        def return_reserved(_context: MechanicContext, key: str = reserved_key) -> MechanicOutcome:
            return MechanicOutcome(result={"status": "committed", key: sentinel})

        owner.register_tool(
            CatalogTool(
                package_id="synthetic.clockwork",
                package_version="1.0.0",
                package_sha256=SYNTHETIC_SHA,
                name=f"private_family_{index}",
                capability="synthetic.counter.increment",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [],
                    "properties": {},
                },
                execute=return_reserved,
            )
        )
        campaign = owner.create_campaign(
            CampaignCreate(
                display_name="Private family probe",
                writer=WriterRole.DM,
                package_lock=_synthetic_lock(),
                turn_id=f"turn-private-{index}",
            )
        )
        command = replace(
            _counter_action(
                campaign.campaign_id,
                action_id=f"private-family-{index}",
                turn_id=f"turn-private-{index}",
                owner_id=f"owner-private-{index}",
            ),
            tool_name=f"private_family_{index}",
            parameters={},
        )
        receipt = owner.execute_action(command)
        snapshot = owner.snapshot(campaign.campaign_id)
        owner.close()
        observed.append(
            (
                reserved_key,
                receipt.status,
                tuple(event.event_type for event in snapshot.events),
            )
        )
        assert receipt.result == {
            "status": "failed",
            "code": "tool_execution_failed",
        }
        assert sentinel.encode() not in database.read_bytes()

    print("F1 ORDER-INDEPENDENT PRIVATE FAMILIES", observed)
    assert all(status is ActionStatus.FAILED for _, status, _ in observed)
    assert all(
        events
        == (
            CanonicalEventType.ACTION_DECLARED,
            CanonicalEventType.TOOL_CALL,
            CanonicalEventType.TOOL_RESULT,
            CanonicalEventType.ACTION_FAILED,
        )
        for _, _, events in observed
    )


def test_owner_startup_rejects_logical_revision_and_chronology_corruption(
    tmp_path: Path,
) -> None:
    failures: list[tuple[str, SchemaIdentityError]] = []
    for corruption in (
        "revision-gap",
        "missing-terminal",
        "event-order",
        "action-revision",
        "mutation-state",
        "idempotency-result",
    ):
        database = tmp_path / f"logical-{corruption}.db"
        owner = _open_counter_owner(database, owner_id=f"owner-before-{corruption}")
        campaign = _create_counter_campaign(owner, campaign_id=f"campaign-{corruption}")
        if corruption != "revision-gap":
            owner.execute_action(
                replace(
                    _counter_action(campaign.campaign_id, action_id=f"action-{corruption}"),
                    owner_id=f"owner-before-{corruption}",
                )
            )
        owner.close()

        with sqlite3.connect(database) as connection:
            if corruption == "revision-gap":
                connection.execute(
                    "UPDATE campaigns SET revision = 7 WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )
            elif corruption == "missing-terminal":
                connection.execute(
                    "DELETE FROM canonical_events "
                    "WHERE campaign_id = ? AND event_type = 'action_committed'",
                    (campaign.campaign_id,),
                )
            elif corruption == "event-order":
                connection.execute(
                    "UPDATE canonical_events SET event_type = CASE sequence "
                    "WHEN 2 THEN 'tool_result' WHEN 3 THEN 'tool_call' ELSE event_type END "
                    "WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )
            elif corruption == "action-revision":
                connection.execute(
                    "UPDATE actions SET expected_revision = 4, resulting_revision = 5 "
                    "WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )
            elif corruption == "mutation-state":
                connection.execute(
                    "UPDATE mutations SET after_json = '999' WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )
            else:
                connection.execute(
                    'UPDATE idempotency_records SET result_json = \'{"status":"forged"}\' '
                    "WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )

        with pytest.raises(SchemaIdentityError) as caught:
            AuthorityOwner.open(database, owner_id=f"owner-after-{corruption}")
        failures.append((corruption, caught.value))

    valid_database = tmp_path / "logical-valid.db"
    valid_owner = _open_counter_owner(valid_database, owner_id="owner-valid-logical")
    committed_campaign = _create_counter_campaign(
        valid_owner,
        campaign_id="campaign-valid-committed",
    )
    valid_owner.execute_action(
        replace(
            _counter_action(
                committed_campaign.campaign_id,
                action_id="valid-committed",
                owner_id="owner-valid-logical",
            ),
        )
    )

    def fail_safely(_context: MechanicContext) -> MechanicOutcome:
        raise RuntimeError("SYNTHETIC_STARTUP_FAILURE")

    valid_owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="startup_failed_action",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
            execute=fail_safely,
        )
    )
    failed_campaign = _create_counter_campaign(
        valid_owner,
        turn_id="turn-failed-logical",
        campaign_id="campaign-valid-failed",
    )
    valid_owner.execute_action(
        replace(
            _counter_action(
                failed_campaign.campaign_id,
                action_id="valid-failed",
                turn_id="turn-failed-logical",
                owner_id="owner-valid-logical",
            ),
            tool_name="startup_failed_action",
            parameters={},
        )
    )
    valid_owner.close()
    reopened = AuthorityOwner.open(valid_database, owner_id="owner-valid-reopened")
    committed = reopened.snapshot(committed_campaign.campaign_id)
    failed = reopened.snapshot(failed_campaign.campaign_id)
    reopened.close()

    print(
        "F1 LOGICAL STARTUP INTEGRITY",
        [(name, error.code, str(error)) for name, error in failures],
        committed.actions[0].status.value,
        failed.actions[0].status.value,
    )
    assert [name for name, _ in failures] == [
        "revision-gap",
        "missing-terminal",
        "event-order",
        "action-revision",
        "mutation-state",
        "idempotency-result",
    ]
    assert all(error.code == "schema_identity_mismatch" for _, error in failures)
    assert all("canonical_events" not in str(error) for _, error in failures)
    assert all("synthetic" not in str(error).casefold() for _, error in failures)
    assert committed.actions[0].status is ActionStatus.COMMITTED
    assert failed.actions[0].status is ActionStatus.FAILED


def test_authoritative_identities_reject_whitespace_before_persistence_and_on_startup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authoritative-identities.db"

    with pytest.raises((CommandRejected, ValueError)):
        invalid_owner = AuthorityOwner.open(database, owner_id="   ")
        invalid_owner.close()

    owner = _open_counter_owner(database)
    invalid_creations: tuple[Callable[[], CampaignCreate], ...] = (
        lambda: CampaignCreate(
            display_name="Valid name",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-valid",
            campaign_id="   ",
        ),
        lambda: CampaignCreate(
            display_name="   ",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-valid",
        ),
        lambda: CampaignCreate(
            display_name="Valid name",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="   ",
        ),
        lambda: CampaignCreate(
            display_name="Valid name",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-valid",
            participants=(Participant("   ", "actor", "synthetic.clockwork", "1.0.0", {}),),
        ),
        lambda: CampaignCreate(
            display_name="Valid name",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-valid",
            initial_state=(StateValue("   ", "counter", 0),),
        ),
    )
    for command_factory in invalid_creations:
        with pytest.raises((CommandRejected, ValueError)):
            owner.create_campaign(command_factory())

    campaign = _create_counter_campaign(owner, campaign_id="campaign-valid-identities")
    with pytest.raises(CommandRejected) as caught:
        owner.execute_action(
            replace(
                _counter_action(campaign.campaign_id, action_id="valid-action"),
                action_id="   ",
                idempotency_key="   ",
                tool_name="   ",
            )
        )
    assert caught.value.code == "invalid_authority_identity"
    assert owner.snapshot(campaign.campaign_id).revision == 0
    owner.close()

    with pytest.raises(ValueError):
        PackageLockEntry(
            package_id="synthetic.invalid",
            version="   ",
            sha256=SYNTHETIC_SHA,
            role=PackageRole.PRIMARY,
            load_order=0,
            dependencies=(),
            capabilities=(),
        )
    with pytest.raises(ValueError):
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256="A" * 64,
            name="tool",
            capability="capability",
            input_schema=_counter_schema(),
            execute=_increment_counter,
        )

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO participants "
            "(campaign_id, participant_id, kind, package_id, package_version, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (campaign.campaign_id, "sql-corrupt", "   ", "synthetic.clockwork", "1.0.0", "{}"),
        )
    with pytest.raises(SchemaIdentityError) as startup:
        AuthorityOwner.open(database, owner_id="owner-after-identity-corruption")

    print(
        "F1 AUTHORITATIVE IDENTITIES",
        caught.value.code,
        startup.value.code,
        campaign.campaign_id,
    )
    assert startup.value.code == "schema_identity_mismatch"
    assert "sql-corrupt" not in str(startup.value)


def test_canonical_authority_identities_reject_surrounding_whitespace_at_every_boundary(
    tmp_path: Path,
) -> None:
    surrounded = (" canonical.identity", "canonical.identity ")
    rejected: list[str] = []

    for invalid in (None, "", "   ", *surrounded):
        with pytest.raises(ValueError):
            validate_authority_identity(invalid, "Probe identity")
    internal_identity = "canonical identity.v1/slot"
    assert validate_authority_identity(internal_identity, "Probe identity") == internal_identity

    for index, invalid_owner_id in enumerate(surrounded):
        owner_database = tmp_path / f"owner-identity-{index}.db"
        with pytest.raises(ValueError):
            AuthorityOwner.open(owner_database, owner_id=invalid_owner_id)
        successor = AuthorityOwner.open(owner_database, owner_id=f"owner.successor/{index}")
        successor.close()
        rejected.append("owner")

    entry = _synthetic_lock().entries[0]
    participant = Participant(
        "participant.valid", "participant kind", "synthetic.clockwork", "1.0.0", {}
    )
    state = StateValue("state namespace", "state key", 0)
    constructor_cases: tuple[tuple[str, Callable[[str], object]], ...] = (
        ("package", lambda value: replace(entry, package_id=value)),
        ("version", lambda value: replace(entry, version=value)),
        ("capability", lambda value: replace(entry, capabilities=(value,))),
        ("participant", lambda value: replace(participant, participant_id=value)),
        ("participant-kind", lambda value: replace(participant, kind=value)),
        ("state-namespace", lambda value: replace(state, namespace=value)),
        ("state-key", lambda value: replace(state, key=value)),
    )
    for label, construct in constructor_cases:
        for invalid in surrounded:
            with pytest.raises(ValueError):
                construct(invalid)
            rejected.append(label)

    package_id = "synthetic clockwork.v1"
    package_version = "release 1.0"
    capability = "synthetic counter.increment/v1"
    tool_name = "increment counter/v1"
    canonical_lock = PackageLock(
        entries=(
            PackageLockEntry(
                package_id=package_id,
                version=package_version,
                sha256=SYNTHETIC_SHA,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(),
                capabilities=(capability,),
            ),
        )
    )

    def canonical_mechanic(context: MechanicContext) -> MechanicOutcome:
        amount = context.parameters["amount"]
        assert isinstance(amount, int)
        before = context.state.get("clockwork state.counter value", 0)
        assert isinstance(before, int)
        return MechanicOutcome(
            result={"status": "committed", "counter": before + amount},
            mutations=(StateMutation("clockwork state", "counter value", before + amount),),
            consequences=({"kind": "counter advanced/v1", "amount": amount},),
        )

    def canonical_tool() -> CatalogTool:
        return CatalogTool(
            package_id=package_id,
            package_version=package_version,
            package_sha256=SYNTHETIC_SHA,
            name=tool_name,
            capability=capability,
            input_schema=_counter_schema(),
            execute=canonical_mechanic,
        )

    database = tmp_path / "canonical-boundaries.db"
    owner_id = "owner canonical/v1"
    owner = AuthorityOwner.open(database, owner_id=owner_id)

    for invalid in surrounded:
        malformed_tool = object.__new__(CatalogTool)
        for name, value in (
            ("package_id", package_id),
            ("package_version", package_version),
            ("package_sha256", SYNTHETIC_SHA),
            ("name", invalid),
            ("capability", capability),
            ("input_schema", _counter_schema()),
            ("execute", canonical_mechanic),
        ):
            object.__setattr__(malformed_tool, name, value)
        with pytest.raises(ValueError):
            owner.register_tool(malformed_tool)
        rejected.append("registration-tool")

    campaign_id = "campaign canonical/v1"
    turn_id = "TURN canonical/v1"
    for field_name in ("campaign_id", "turn_id"):
        for invalid in surrounded:
            create_command = CampaignCreate(
                display_name="Canonical campaign",
                writer=WriterRole.DM,
                package_lock=canonical_lock,
                turn_id=turn_id,
                campaign_id=campaign_id,
            )
            invalid_create = (
                replace(create_command, campaign_id=invalid)
                if field_name == "campaign_id"
                else replace(create_command, turn_id=invalid)
            )
            with pytest.raises(CommandRejected) as creation:
                owner.create_campaign(invalid_create)
            assert creation.value.code == "invalid_authority_identity"
            rejected.append(f"creation-{field_name}")

    owner.register_tool(canonical_tool())
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Canonical campaign",
            writer=WriterRole.DM,
            package_lock=canonical_lock,
            turn_id=turn_id,
            campaign_id=campaign_id,
            participants=(
                Participant(
                    "participant gardener/v1",
                    "garden caretaker",
                    package_id,
                    package_version,
                    {"label": "Canonical gardener"},
                ),
            ),
            initial_state=(StateValue("clockwork state", "counter value", 0),),
        )
    )
    assert len(owner.catalog(campaign.campaign_id)) == 1
    reloaded_package_sha = owner.snapshot(campaign.campaign_id).package_lock.entries[0].sha256

    action_command = ActionCommand(
        campaign_id=campaign_id,
        action_id="action canonical/v1",
        idempotency_key="idempotency canonical/v1",
        owner_id=owner_id,
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id=turn_id,
        package_id=package_id,
        tool_name=tool_name,
        parameters={"amount": 2},
    )
    for invalid in surrounded:
        invalid_commands = (
            ("campaign_id", replace(action_command, campaign_id=invalid)),
            ("action_id", replace(action_command, action_id=invalid)),
            ("idempotency_key", replace(action_command, idempotency_key=invalid)),
            ("owner_id", replace(action_command, owner_id=invalid)),
            ("turn_id", replace(action_command, turn_id=invalid)),
            ("package_id", replace(action_command, package_id=invalid)),
            ("tool_name", replace(action_command, tool_name=invalid)),
        )
        for field_name, invalid_command in invalid_commands:
            with pytest.raises(CommandRejected) as admission:
                owner.execute_action(invalid_command)
            assert admission.value.code == "invalid_authority_identity"
            rejected.append(f"command-{field_name}")
    assert owner.snapshot(campaign_id).revision == 0

    receipt = owner.execute_action(action_command)
    replay = owner.execute_action(action_command)
    assert replay == receipt
    owner.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0] == 1

    reopened_owner_id = "owner reopened/v1"
    reopened = AuthorityOwner.open(database, owner_id=reopened_owner_id)
    reopened.register_tool(replace(canonical_tool(), package_sha256=reloaded_package_sha))
    reloaded = reopened.snapshot(campaign_id)
    reopened_replay = reopened.execute_action(replace(action_command, owner_id=reopened_owner_id))
    reopened.close()

    assert reopened_replay == receipt
    assert reloaded.campaign_id == campaign_id
    assert tuple(reloaded.turns) == (turn_id,)
    assert reloaded.actions[0].action_id == action_command.action_id
    assert reloaded.actions[0].tool_name == tool_name
    assert reloaded.state == {"clockwork state.counter value": 2}
    assert reloaded.mutations[0].namespace == "clockwork state"
    assert reloaded.mutations[0].key == "counter value"
    assert reloaded.consequences[0].payload["kind"] == "counter advanced/v1"

    outcome_database = tmp_path / "outcome-identities.db"
    outcome_owner = _open_counter_owner(outcome_database, owner_id="owner-outcome")
    invalid_outcomes: tuple[tuple[str, MechanicOutcome], ...] = (
        (
            "mutation-namespace-leading",
            MechanicOutcome(result={}, mutations=(StateMutation(" clockwork", "counter", 1),)),
        ),
        (
            "mutation-namespace-trailing",
            MechanicOutcome(result={}, mutations=(StateMutation("clockwork ", "counter", 1),)),
        ),
        (
            "mutation-key-leading",
            MechanicOutcome(result={}, mutations=(StateMutation("clockwork", " counter", 1),)),
        ),
        (
            "mutation-key-trailing",
            MechanicOutcome(result={}, mutations=(StateMutation("clockwork", "counter ", 1),)),
        ),
        (
            "consequence-leading",
            MechanicOutcome(result={}, consequences=({"kind": " consequence.kind"},)),
        ),
        (
            "consequence-trailing",
            MechanicOutcome(result={}, consequences=({"kind": "consequence.kind "},)),
        ),
    )

    def fixed_outcome_mechanic(
        outcome: MechanicOutcome,
    ) -> Callable[[MechanicContext], MechanicOutcome]:
        def execute(_context: MechanicContext) -> MechanicOutcome:
            return outcome

        return execute

    for name, invalid_outcome in invalid_outcomes:
        outcome_owner.register_tool(
            CatalogTool(
                package_id="synthetic.clockwork",
                package_version="1.0.0",
                package_sha256=SYNTHETIC_SHA,
                name=name,
                capability="synthetic.counter.increment",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [],
                    "properties": {},
                },
                execute=fixed_outcome_mechanic(invalid_outcome),
            )
        )
    outcome_campaign = _create_counter_campaign(
        outcome_owner, campaign_id="campaign-outcome-identities"
    )

    for revision, (name, _invalid_outcome) in enumerate(invalid_outcomes):
        failed = outcome_owner.execute_action(
            replace(
                _counter_action(
                    outcome_campaign.campaign_id,
                    action_id=f"action-{name}",
                    owner_id="owner-outcome",
                    expected_revision=revision,
                ),
                tool_name=name,
                parameters={},
            )
        )
        assert failed.status is ActionStatus.FAILED
        rejected.append(name)
    outcome_snapshot = outcome_owner.snapshot(outcome_campaign.campaign_id)
    outcome_owner.close()
    assert outcome_snapshot.mutations == ()
    assert outcome_snapshot.consequences == ()
    assert outcome_snapshot.state == {"clockwork.counter": 0}

    corrupt_database = tmp_path / "direct-sql-surrounding-whitespace.db"
    corrupt_owner = _open_counter_owner(corrupt_database, owner_id="owner-corrupt-before")
    corrupt_campaign = corrupt_owner.create_campaign(
        CampaignCreate(
            display_name="Corruption probe",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-corrupt",
            campaign_id="campaign-corrupt",
            participants=(
                Participant("participant-corrupt", "actor", "synthetic.clockwork", "1.0.0", {}),
            ),
        )
    )
    corrupt_owner.close()
    with sqlite3.connect(corrupt_database) as connection:
        connection.execute(
            "UPDATE participants SET kind = ? WHERE campaign_id = ?",
            (" actor ", corrupt_campaign.campaign_id),
        )
    startup_failures: list[str] = []
    for attempt in range(2):
        with pytest.raises(SchemaIdentityError) as startup:
            AuthorityOwner.open(corrupt_database, owner_id=f"owner-corrupt-after/{attempt}")
        startup_failures.append(startup.value.code)

    print(
        "F1 CANONICAL AUTHORITY IDENTITIES",
        len(rejected),
        receipt.result,
        reopened_replay.result,
        startup_failures,
    )
    assert startup_failures == ["schema_identity_mismatch", "schema_identity_mismatch"]


def test_lifecycle_writer_and_turn_chronology_fail_closed_at_admission_and_startup(
    tmp_path: Path,
) -> None:
    admission_database = tmp_path / "lifecycle-admission.db"
    owner = _open_counter_owner(admission_database)
    with pytest.raises(CommandRejected) as admission:
        owner.create_campaign(
            CampaignCreate(
                display_name="Writerless active campaign",
                writer=WriterRole.NONE,
                package_lock=_synthetic_lock(),
                turn_id="turn-writerless",
                lifecycle=LifecycleState.ACTIVE,
            )
        )
    owner.close()
    assert admission.value.code == "invalid_lifecycle_writer"

    failures: list[str] = []
    for corruption in ("archived-dm", "missing-turn", "turn-after-action"):
        database = tmp_path / f"chronology-{corruption}.db"
        before = _open_counter_owner(database, owner_id=f"owner-before-{corruption}")
        campaign = _create_counter_campaign(
            before,
            campaign_id=f"campaign-{corruption}",
        )
        if corruption == "turn-after-action":
            before.execute_action(
                replace(
                    _counter_action(campaign.campaign_id, action_id="chronology-action"),
                    owner_id=f"owner-before-{corruption}",
                )
            )
        before.close()

        with sqlite3.connect(database) as connection:
            if corruption == "archived-dm":
                connection.execute(
                    "UPDATE campaigns SET lifecycle = 'archived', writer = 'dm' "
                    "WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )
            elif corruption == "missing-turn":
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "DELETE FROM turns WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )
            else:
                connection.execute(
                    "UPDATE turns SET created_revision = 1 WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )

        with pytest.raises(SchemaIdentityError) as caught:
            AuthorityOwner.open(database, owner_id=f"owner-after-{corruption}")
        failures.append(corruption)
        assert caught.value.code == "schema_identity_mismatch"
        assert corruption not in str(caught.value)

    print("F1 LIFECYCLE/TURN CHRONOLOGY", admission.value.code, failures)
    assert failures == ["archived-dm", "missing-turn", "turn-after-action"]


def test_private_thought_and_internal_model_channel_families_never_persist(
    tmp_path: Path,
) -> None:
    sentinel = "PRIVATE_THOUGHT_WAVE_E_SENTINEL"
    reserved = (
        "private_thought",
        "privateThought",
        "privatethought",
        "raw_thought",
        "providerThought",
        "hidden_thought",
        "internalMonologue",
        "private_monologue",
        "scratchpad",
        "provider_model_response",
        "rawModelResponse",
    )
    statuses: list[ActionStatus] = []

    for index, key in enumerate(reserved):
        database = tmp_path / f"private-thought-{index}.db"
        owner = _open_counter_owner(database, owner_id=f"owner-private-thought-{index}")

        def private_output(_context: MechanicContext, channel: str = key) -> MechanicOutcome:
            return MechanicOutcome(result={"status": "committed", channel: sentinel})

        owner.register_tool(
            CatalogTool(
                package_id="synthetic.clockwork",
                package_version="1.0.0",
                package_sha256=SYNTHETIC_SHA,
                name=f"private_thought_tool_{index}",
                capability="synthetic.counter.increment",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [],
                    "properties": {},
                },
                execute=private_output,
            )
        )
        campaign = _create_counter_campaign(
            owner,
            turn_id=f"turn-private-thought-{index}",
            campaign_id=f"campaign-private-thought-{index}",
        )
        receipt = owner.execute_action(
            replace(
                _counter_action(
                    campaign.campaign_id,
                    action_id=f"private-thought-{index}",
                    turn_id=f"turn-private-thought-{index}",
                    owner_id=f"owner-private-thought-{index}",
                ),
                tool_name=f"private_thought_tool_{index}",
                parameters={},
            )
        )
        statuses.append(receipt.status)
        owner.close()
        assert sentinel.encode() not in database.read_bytes()

    safe_database = tmp_path / "safe-thought.db"
    safe_owner = _open_counter_owner(safe_database, owner_id="owner-safe-thought")

    def safe_thought(_context: MechanicContext) -> MechanicOutcome:
        return MechanicOutcome(result={"status": "committed", "thought": "ordinary idea"})

    safe_owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="safe_thought_tool",
            capability="synthetic.counter.increment",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
            execute=safe_thought,
        )
    )
    safe_campaign = _create_counter_campaign(
        safe_owner,
        turn_id="turn-safe-thought",
        campaign_id="campaign-safe-thought",
    )
    safe_receipt = safe_owner.execute_action(
        replace(
            _counter_action(
                safe_campaign.campaign_id,
                action_id="safe-thought",
                turn_id="turn-safe-thought",
                owner_id="owner-safe-thought",
            ),
            tool_name="safe_thought_tool",
            parameters={},
        )
    )
    safe_owner.close()

    print("F1 PRIVATE THOUGHT FAMILY", [status.value for status in statuses])
    assert statuses == [ActionStatus.FAILED] * len(reserved)
    assert safe_receipt.status is ActionStatus.COMMITTED
    assert safe_receipt.result["thought"] == "ordinary idea"


def test_startup_requires_exact_strict_canonical_json_bytes_in_every_variant(
    tmp_path: Path,
) -> None:
    sentinel = "HIDDEN_DUPLICATE_PRIVATE_SENTINEL"
    rejected: list[str] = []
    for corruption in ("duplicate-private", "whitespace", "key-order", "nonfinite"):
        database = tmp_path / f"canonical-json-{corruption}.db"
        owner = _open_counter_owner(database, owner_id=f"owner-json-before-{corruption}")
        campaign = _create_counter_campaign(
            owner,
            campaign_id=f"campaign-json-{corruption}",
        )
        owner.execute_action(
            replace(
                _counter_action(campaign.campaign_id, action_id=f"action-json-{corruption}"),
                owner_id=f"owner-json-before-{corruption}",
            )
        )
        owner.close()

        with sqlite3.connect(database) as connection:
            if corruption == "duplicate-private":
                connection.execute(
                    "UPDATE actions SET result_json = ? WHERE campaign_id = ?",
                    (
                        '{"status":{"private_thought":"'
                        + sentinel
                        + '"},"counter":1,"status":"committed"}',
                        campaign.campaign_id,
                    ),
                )
            elif corruption == "whitespace":
                original = str(
                    connection.execute(
                        "SELECT payload_json FROM actions WHERE campaign_id = ?",
                        (campaign.campaign_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    "UPDATE actions SET payload_json = ? WHERE campaign_id = ?",
                    (" " + original, campaign.campaign_id),
                )
            elif corruption == "key-order":
                connection.execute(
                    "UPDATE actions SET result_json = ? WHERE campaign_id = ?",
                    ('{"status":"committed","counter":1}', campaign.campaign_id),
                )
            else:
                connection.execute(
                    "UPDATE campaign_state SET value_json = 'NaN' WHERE campaign_id = ?",
                    (campaign.campaign_id,),
                )

        with pytest.raises(SchemaIdentityError) as caught:
            AuthorityOwner.open(database, owner_id=f"owner-json-after-{corruption}")
        rejected.append(corruption)
        assert caught.value.code == "schema_identity_mismatch"
        assert sentinel not in str(caught.value)

    valid_database = tmp_path / "canonical-json-valid.db"
    valid_owner = _open_counter_owner(valid_database, owner_id="owner-json-valid")
    valid_campaign = _create_counter_campaign(valid_owner, campaign_id="campaign-json-valid")
    valid_owner.execute_action(
        replace(
            _counter_action(valid_campaign.campaign_id, action_id="action-json-valid"),
            owner_id="owner-json-valid",
        )
    )
    valid_owner.close()
    reopened = AuthorityOwner.open(valid_database, owner_id="owner-json-reopened")
    reloaded = reopened.snapshot(valid_campaign.campaign_id)
    reopened.close()

    print("F1 STRICT CANONICAL JSON", rejected, reloaded.revision)
    assert rejected == ["duplicate-private", "whitespace", "key-order", "nonfinite"]
    assert reloaded.revision == 1


def test_actions_persist_exact_historical_package_pin_and_reject_lock_swap(
    tmp_path: Path,
) -> None:
    database = tmp_path / "historical-pin.db"
    v2_sha = "c" * 64
    v2_lock = PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.clockwork",
                version="2.0.0",
                sha256=v2_sha,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(),
                capabilities=("synthetic.counter.increment",),
            ),
        )
    )

    def versioned(version: str) -> Callable[[MechanicContext], MechanicOutcome]:
        def execute(_context: MechanicContext) -> MechanicOutcome:
            return MechanicOutcome(result={"status": "committed", "version": version})

        return execute

    v1_tool = CatalogTool(
        package_id="synthetic.clockwork",
        package_version="1.0.0",
        package_sha256=SYNTHETIC_SHA,
        name="increment_counter",
        capability="synthetic.counter.increment",
        input_schema=_counter_schema(),
        execute=versioned("1.0.0"),
    )
    v2_tool = replace(
        v1_tool,
        package_version="2.0.0",
        package_sha256=v2_sha,
        execute=versioned("2.0.0"),
    )
    owner = AuthorityOwner.open(database, owner_id="owner-pin-before")
    owner.register_tool(v1_tool)
    owner.register_tool(v2_tool)
    campaign_v1 = owner.create_campaign(
        CampaignCreate(
            display_name="Historical pin v1",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-pin-v1",
            campaign_id="campaign-pin-v1",
        )
    )
    campaign_v2 = owner.create_campaign(
        CampaignCreate(
            display_name="Historical pin v2",
            writer=WriterRole.DM,
            package_lock=v2_lock,
            turn_id="turn-pin-v2",
            campaign_id="campaign-pin-v2",
        )
    )
    command_v1 = replace(
        _counter_action(
            campaign_v1.campaign_id,
            action_id="action-pin-v1",
            turn_id="turn-pin-v1",
            owner_id="owner-pin-before",
        ),
        parameters={"amount": 1},
    )
    command_v2 = replace(
        _counter_action(
            campaign_v2.campaign_id,
            action_id="action-pin-v2",
            turn_id="turn-pin-v2",
            owner_id="owner-pin-before",
        ),
        parameters={"amount": 1},
    )
    receipt_v1 = owner.execute_action(command_v1)
    receipt_v2 = owner.execute_action(command_v2)
    snapshot_v1 = owner.snapshot(campaign_v1.campaign_id)
    snapshot_v2 = owner.snapshot(campaign_v2.campaign_id)
    owner.close()

    actual_v1_sha = snapshot_v1.package_lock.entries[0].sha256
    actual_v2_sha = snapshot_v2.package_lock.entries[0].sha256
    assert (snapshot_v1.actions[0].package_version, snapshot_v1.actions[0].package_sha256) == (
        "1.0.0",
        actual_v1_sha,
    )
    assert (snapshot_v2.actions[0].package_version, snapshot_v2.actions[0].package_sha256) == (
        "2.0.0",
        actual_v2_sha,
    )
    assert snapshot_v1.events[1].safe_payload == {
        "package_id": "synthetic.clockwork",
        "package_version": "1.0.0",
        "package_sha256": actual_v1_sha,
        "tool_name": "increment_counter",
        "parameters": {"amount": 1},
    }

    reopened = AuthorityOwner.open(database, owner_id="owner-pin-reopened")
    replay = reopened.execute_action(replace(command_v1, owner_id="owner-pin-reopened"))
    assert replay == receipt_v1
    with pytest.raises(IdempotencyConflict):
        reopened.execute_action(
            replace(
                command_v1,
                owner_id="owner-pin-reopened",
                tool_name="different_tool",
            )
        )
    assert reopened.snapshot(campaign_v2.campaign_id).actions[0].result == receipt_v2.result
    reopened.close()

    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM campaign_package_memberships WHERE campaign_id = ?",
            (campaign_v1.campaign_id,),
        )
        connection.execute(
            "INSERT INTO campaign_package_memberships VALUES (?, ?, ?, ?, 'primary', 0)",
            (campaign_v1.campaign_id, "synthetic.clockwork", "2.0.0", v2_sha),
        )
        swapped_lock = json.dumps(
            [
                {
                    "id": "synthetic.clockwork",
                    "load_order": 0,
                    "role": "primary",
                    "sha256": v2_sha,
                    "version": "2.0.0",
                }
            ],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        connection.execute(
            "UPDATE campaigns SET lock_hash = ? WHERE campaign_id = ?",
            (swapped_lock, campaign_v1.campaign_id),
        )

    with pytest.raises(SchemaIdentityError) as swapped:
        AuthorityOwner.open(database, owner_id="owner-pin-after-swap")

    print(
        "F1 HISTORICAL EXACT PIN",
        receipt_v1.result,
        receipt_v2.result,
        replay.result,
        swapped.value.code,
    )
    assert swapped.value.code == "schema_identity_mismatch"


def test_mechanic_context_receives_detached_immutable_participant_projection(
    tmp_path: Path,
) -> None:
    """A package may read campaign participants without receiving participant authority."""

    observed: Mapping[str, object] | None = None

    def inspect_participant(context: MechanicContext) -> MechanicOutcome:
        nonlocal observed
        observed = context.participants
        participant = context.participants["gardener"]
        assert isinstance(participant, Mapping)
        data = participant["data"]
        assert isinstance(data, Mapping)
        assert data["ability"] == 12
        with pytest.raises(TypeError):
            data["ability"] = 99  # type: ignore[index]
        return _increment_counter(context)

    owner = AuthorityOwner.open(tmp_path / "participant-context.db", owner_id="owner-alpha")
    owner.register_tool(
        CatalogTool(
            package_id="synthetic.clockwork",
            package_version="1.0.0",
            package_sha256=SYNTHETIC_SHA,
            name="increment_counter",
            capability="synthetic.counter.increment",
            input_schema=_counter_schema(),
            execute=inspect_participant,
        )
    )
    campaign = owner.create_campaign(
        CampaignCreate(
            display_name="Participant context",
            writer=WriterRole.DM,
            package_lock=_synthetic_lock(),
            turn_id="turn-participant-context",
            participants=(
                Participant(
                    participant_id="gardener",
                    kind="caretaker",
                    package_id="synthetic.clockwork",
                    package_version="1.0.0",
                    data={"ability": 12},
                ),
            ),
            initial_state=(StateValue("clockwork", "counter", 0),),
        )
    )
    receipt = owner.execute_action(
        ActionCommand(
            campaign_id=campaign.campaign_id,
            action_id="action-participant-context",
            idempotency_key="idempotency-participant-context",
            owner_id="owner-alpha",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn-participant-context",
            package_id="synthetic.clockwork",
            tool_name="increment_counter",
            parameters={"amount": 1},
        )
    )
    snapshot = owner.snapshot(campaign.campaign_id)
    owner.close()

    print("F1 PARTICIPANT CONTEXT", receipt.status.value, observed)
    assert receipt.status is ActionStatus.COMMITTED
    assert observed is not None
    assert snapshot.participants[0].data == {"ability": 12}
