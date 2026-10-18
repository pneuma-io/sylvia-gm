"""F3 capability catalog, quiescence, migration, and activation probes."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    CatalogTool,
)
from sylvia_gm.application.package_library import declaration_admitted
from sylvia_gm.domain.authority import (
    MechanicContext,
    MechanicOutcome,
    PackageLock,
    StateMutation,
    StateValue,
    TurnStatus,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, SchemaIdentityError
from sylvia_gm.domain.package_library import (
    ActivationCommand,
    ActivationStatus,
    StatePredicate,
    ToolDeclaration,
)

_publication_path = Path(__file__).with_name("test_alpha_f3_package_publication.py")
_publication_spec = importlib.util.spec_from_file_location(
    "f3_publication_fixtures", _publication_path
)
assert _publication_spec is not None and _publication_spec.loader is not None
_publication = importlib.util.module_from_spec(_publication_spec)
_publication_spec.loader.exec_module(_publication)
_archive = _publication._archive
_clockwork = _publication._clockwork
_dependency = _publication._dependency
_install = _publication._install
_zero_capability_primary = _publication._zero_capability_primary
_package_record = _publication._package_record
_tool = _publication._tool
_surface = _publication._surface


def _mechanic(context: MechanicContext) -> MechanicOutcome:
    current = int(context.state.get("clockwork.counter", 0))
    steps = int(context.parameters["steps"])
    return MechanicOutcome(
        result=owned_payload({"status": "advanced", "value": current + steps}),
        mutations=(StateMutation("clockwork", "counter", current + steps),),
    )


def _registered_tool(record: Mapping[str, object]) -> CatalogTool:
    declared = record["tools"]
    assert isinstance(declared, list)
    tool = declared[0]
    assert isinstance(tool, dict)
    schema = tool["input_schema"]
    assert isinstance(schema, dict)
    return CatalogTool(
        package_id=str(record["id"]),
        package_version=str(record["version"]),
        package_sha256=str(record["content_sha256"]),
        name=str(tool["name"]),
        capability=str(tool["capability"]),
        input_schema=schema,
        execute=_mechanic,
    )


def _open_versioned(
    tmp_path: Path,
    *,
    state_predicates: tuple[dict[str, object], ...] = (),
) -> tuple[AuthorityOwner, object, object, dict[str, object], dict[str, object]]:
    owner = AuthorityOwner.open(tmp_path / "authority.db", "owner-f3", disconnect_grace_seconds=5)
    old_record, old_files = _clockwork("1", payload=b'{"revision":1}')
    new_record, new_files = _clockwork("2", payload=b'{"revision":2}')
    if state_predicates:
        declared = dict(new_record["tools"][0])  # type: ignore[index]
        declared["state_predicates"] = list(state_predicates)
        new_record["tools"] = [declared]
        from sylvia_gm.domain.package_library import content_root_for_record

        new_record["content_sha256"] = content_root_for_record(new_record)
    old_candidate = _install(owner, ((old_record, old_files),), "old")
    new_candidate = _install(owner, ((new_record, new_files),), "new")
    owner.register_tool(_registered_tool(old_record))
    owner.register_tool(_registered_tool(new_record))
    return owner, old_candidate, new_candidate, old_record, new_record


def _campaign(owner: AuthorityOwner, candidate: object, *, state: int = 0) -> str:
    lock = candidate.package_lock  # type: ignore[attr-defined]
    campaign = owner.create_campaign(
        CampaignCreate(
            campaign_id="campaign-f3-activation",
            display_name="Synthetic time observatory",
            writer=WriterRole.DM,
            package_lock=lock,
            turn_id="turn-f3-activation",
            initial_state=(StateValue("clockwork", "counter", state),),
        )
    )
    return campaign.campaign_id


def _activation(
    owner: AuthorityOwner,
    campaign_id: str,
    candidate: object,
    *,
    activation_id: str = "activation-f3",
    expected_revision: int = 0,
    migration_id: str = "migration-clockwork-v1-v2",
) -> ActivationCommand:
    current = owner.current_package_lock(campaign_id)
    return ActivationCommand(
        campaign_id=campaign_id,
        candidate_id=candidate.candidate_id,  # type: ignore[attr-defined]
        activation_id=activation_id,
        expected_revision=expected_revision,
        expected_lock_id=current.lock_id,
        migration_id=migration_id,
    )


def _migrate(state: Mapping[str, object]) -> Mapping[str, object]:
    return {**state, "clockwork.counter": int(state["clockwork.counter"]) + 100}


def _dotted_package_closure(
    *,
    primary_id: str = "synthetic.rules-core.v1",
    setting_id: str = "synthetic.campaign-setting.v1",
) -> tuple[
    tuple[dict[str, object], dict[str, bytes]],
    tuple[dict[str, object], dict[str, bytes]],
]:
    setting_files = {"data/setting.json": b'{"setting":true}'}
    setting = _package_record(
        setting_id,
        "1.0.0",
        setting_files,
        role="dependency",
        label="Synthetic dotted setting",
    )
    primary_files = {"data/rules.json": b'{"rules":true}'}
    primary = _package_record(
        primary_id,
        "1.0.0",
        primary_files,
        dependencies=(_dependency(setting, "campaign-setting"),),
        label="Synthetic dotted rules",
    )
    return (setting, setting_files), (primary, primary_files)


def _complete(owner: AuthorityOwner, campaign_id: str) -> None:
    owner.complete_turn(campaign_id, "turn-f3-activation")
    assert owner.snapshot(campaign_id).turns["turn-f3-activation"] is TurnStatus.COMPLETED


def test_empty_campaign_activates_dotted_multi_package_state_without_preseed_and_replays(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.db"
    primary_id = "synthetic.rules-core.v1"
    setting_id = "synthetic.campaign-setting.v1"
    migration_calls: list[int] = []
    with AuthorityOwner.open(database, "owner-f3") as owner:
        candidate = _install(owner, _dotted_package_closure(), "dotted-closure")
        campaign = owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-f3-activation",
                display_name="Empty dotted activation",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn-f3-activation",
            )
        )
        assert owner.snapshot(campaign.campaign_id).state == {}
        _complete(owner, campaign.campaign_id)
        command = _activation(
            owner,
            campaign.campaign_id,
            candidate,
            activation_id="activation-dotted-closure",
            migration_id="initialize-dotted-closure",
        )

        def initialize(_state: Mapping[str, object]) -> Mapping[str, object]:
            migration_calls.append(1)
            return {
                f"{primary_id}.state": {"ready": True},
                f"{setting_id}.state": {"ready": True},
            }

        committed = owner.activate_package(command, initialize)
        replayed = owner.activate_package(command, initialize)
        assert replayed.replayed is True
        assert replayed.new_lock_id == committed.new_lock_id
        assert migration_calls == [1]
        assert owner.snapshot(campaign.campaign_id).state == {
            f"{primary_id}.state": {"ready": True},
            f"{setting_id}.state": {"ready": True},
        }
        connection = owner._store._connect()
        try:
            rows = connection.execute(
                "SELECT namespace, state_key, owner_package_id "
                "FROM campaign_state JOIN campaign_state_namespace_owners "
                "USING (campaign_id, namespace) WHERE campaign_id = ? ORDER BY namespace",
                (campaign.campaign_id,),
            ).fetchall()
        finally:
            connection.close()
        assert [tuple(row) for row in rows] == [
            (setting_id, "state", setting_id),
            (primary_id, "state", primary_id),
        ]

    with AuthorityOwner.open(database, "owner-f3-reopened") as reopened:
        assert reopened.activate_package(
            command,
            lambda _state: (_ for _ in ()).throw(AssertionError("migration repeated")),
        ).replayed
        assert reopened.snapshot("campaign-f3-activation").state[
            f"{primary_id}.state"
        ] == {"ready": True}


def test_empty_multi_package_activation_rejects_undeclared_or_ambiguous_state_atomically(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "undeclared",
            _dotted_package_closure(),
            {"synthetic.unknown.state": {}},
        ),
        (
            "ambiguous",
            _dotted_package_closure(
                primary_id="synthetic.rules",
                setting_id="synthetic.rules.setting",
            ),
            {"synthetic.rules.setting.state": {}},
        ),
    )
    for label, closure, migrated in cases:
        case = tmp_path / label
        case.mkdir()
        with AuthorityOwner.open(case / "authority.db", f"owner-{label}") as owner:
            candidate = _install(owner, closure, f"closure-{label}")
            campaign = owner.create_campaign(
                CampaignCreate(
                    campaign_id="campaign-f3-activation",
                    display_name=f"Rejected {label} activation",
                    writer=WriterRole.DM,
                    package_lock=PackageLock(entries=()),
                    turn_id="turn-f3-activation",
                )
            )
            _complete(owner, campaign.campaign_id)
            before = owner.snapshot(campaign.campaign_id)
            before_lock = owner.current_package_lock(campaign.campaign_id)
            with pytest.raises(CommandRejected) as rejected:
                owner.activate_package(
                    _activation(
                        owner,
                        campaign.campaign_id,
                        candidate,
                        activation_id=f"activation-{label}",
                    ),
                    lambda _state, result=migrated: result,
                )
            assert rejected.value.code in {
                "migration_invalid",
                "state_namespace_ownership_invalid",
            }
            assert owner.snapshot(campaign.campaign_id) == before
            assert owner.current_package_lock(campaign.campaign_id) == before_lock


class _ActivationCommitProxy:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        activation_id: str,
        mode: str,
        campaign_id: str,
    ) -> None:
        self._connection = connection
        self._activation_id = activation_id
        self._mode = mode
        self._campaign_id = campaign_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        activation = self._connection.execute(
            "SELECT 1 FROM package_activations WHERE activation_id = ?",
            (self._activation_id,),
        ).fetchone()
        if activation is None:
            self._connection.commit()
            return
        if self._mode == "durable":
            self._connection.commit()
        elif self._mode == "incoherent":
            self._connection.rollback()
            self._connection.execute(
                "UPDATE campaign_state SET value_json = '999' WHERE campaign_id = ?",
                (self._campaign_id,),
            )
            self._connection.commit()
        raise sqlite3.OperationalError("synthetic commit transport detail")


def test_f3_catalog_uses_active_lock_capability_state_turn_writer_transition_and_exact_schema(
    tmp_path: Path,
) -> None:
    predicate = {"namespace": "clockwork", "key": "counter", "equals": 7}
    owner, old, new, old_record, new_record = _open_versioned(
        tmp_path, state_predicates=(predicate,)
    )
    try:
        campaign_id = _campaign(owner, old, state=7)
        active = owner.catalog(campaign_id)
        assert [tool.name for tool in active] == ["advance"]
        assert active[0].input_schema == old_record["tools"][0]["input_schema"]  # type: ignore[index]
        wrong_schema = dict(new_record["tools"][0]["input_schema"])  # type: ignore[index]
        wrong_schema["required"] = []
        with pytest.raises(CommandRejected, match="schema"):
            owner.register_tool(
                CatalogTool(
                    package_id=str(new_record["id"]),
                    package_version=str(new_record["version"]),
                    package_sha256=str(new_record["content_sha256"]),
                    name="wrong-schema",
                    capability="clockwork.counter",
                    input_schema=wrong_schema,
                    execute=_mechanic,
                )
            )
        _complete(owner, campaign_id)
        result = owner.activate_package(_activation(owner, campaign_id, new), _migrate)
        assert result.status is ActivationStatus.COMMITTED
        assert [
            tool.name for tool in owner.catalog(campaign_id)
        ] == []  # migrated value no longer equals 7
        assert owner.snapshot(campaign_id).state["clockwork.counter"] == 107
        surfaces = owner.package_surfaces(campaign_id)
        assert surfaces.prompt_fragment_ids == ("clockwork.prompt",)
        assert surfaces.ui_surface_ids == ("clockwork.panel",)
    finally:
        owner.close()
    print("F3 CATALOG", result.new_lock_id, result.resulting_revision, "state predicate closed")


def test_f3_declaration_availability_reads_nested_package_state_and_missing_negation() -> None:
    declaration = ToolDeclaration(
        name="start_encounter",
        capability="clockwork.encounter",
        input_schema=owned_payload({"type": "object"}),
        state_predicates=(
            StatePredicate("clockwork", "combat.encounter_active", False),
            StatePredicate("clockwork", "combat.turn_active", True, negated=True),
        ),
        turn_statuses=("open",),
        writers=("dm",),
    )
    basis = {
        "clockwork.combat": {
            "encounter_active": False,
            "turn_active": False,
        }
    }

    assert declaration_admitted(
        declaration,
        state=basis,
        turn_status="open",
        writer="dm",
        declared_capabilities=frozenset({"clockwork.encounter"}),
    )
    assert declaration_admitted(
        declaration,
        state={},
        turn_status="open",
        writer="dm",
        declared_capabilities=frozenset({"clockwork.encounter"}),
    ) is False  # explicit false is required by the first predicate
    assert not declaration_admitted(
        declaration,
        state={"clockwork.combat": {"encounter_active": True, "turn_active": False}},
        turn_status="open",
        writer="dm",
        declared_capabilities=frozenset({"clockwork.encounter"}),
    )
    assert not declaration_admitted(
        declaration,
        state={"clockwork.combat": {"encounter_active": False, "turn_active": True}},
        turn_status="open",
        writer="dm",
        declared_capabilities=frozenset({"clockwork.encounter"}),
    )

    absent_is_not_true = ToolDeclaration(
        name="start_encounter",
        capability="clockwork.encounter",
        input_schema=owned_payload({"type": "object"}),
        state_predicates=(
            StatePredicate("clockwork", "combat.encounter_active", True, negated=True),
        ),
        turn_statuses=("open",),
        writers=("dm",),
    )
    assert declaration_admitted(
        absent_is_not_true,
        state={},
        turn_status="open",
        writer="dm",
        declared_capabilities=frozenset({"clockwork.encounter"}),
    )


def test_f3_no_rules_and_synthetic_non_dnd_surfaces_have_no_undeclared_mechanics(
    tmp_path: Path,
) -> None:
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-f3") as owner:
        no_rules_campaign = owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-no-rules-f3",
                display_name="Rule-less conversation",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn-no-rules",
            )
        )
        assert owner.catalog(no_rules_campaign.campaign_id) == ()
        assert owner.package_surfaces(no_rules_campaign.campaign_id).prompt_fragment_ids == ()
        assert owner.package_surfaces(no_rules_campaign.campaign_id).ui_surface_ids == ()
        assert owner.package_candidates() == ()
        record, files = _clockwork()
        candidate = _install(owner, ((record, files),), "synthetic")
        owner.register_tool(_registered_tool(record))
        synthetic = owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-synthetic-f3",
                display_name="Clock observatory",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn-synthetic",
                initial_state=(StateValue("clockwork", "counter", 7),),
            )
        )
        names = [tool.name for tool in owner.catalog(synthetic.campaign_id)]
        projection = owner.package_surfaces(synthetic.campaign_id)
        rendered = repr((names, projection)).casefold()
        assert names == ["advance"]
        assert projection.ui_surfaces[0]["title"] == "Clockwork"
        assert owner.campaign_panels(synthetic.campaign_id)["surfaces"][0]["data"] == 7
        assert "d&d" not in rendered and "armor" not in rendered and "spell" not in rendered
    print("F3 NEUTRAL", "no-rules=0", names, projection)


def test_f3_unrelated_candidate_campaign_or_capability_changes_no_tool_prompt_or_ui_projection(
    tmp_path: Path,
) -> None:
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-f3") as owner:
        record, files = _clockwork()
        active_candidate = _install(owner, ((record, files),), "active")
        owner.register_tool(_registered_tool(record))
        campaign_id = _campaign(owner, active_candidate)
        before_catalog = owner.catalog_definition_bytes(campaign_id)
        before_surfaces = owner.package_surface_bytes(campaign_id)
        unrelated_files = {"data/weather.json": b'{"weather":"clear"}'}
        unrelated = _package_record(
            "synthetic.weather",
            "1",
            unrelated_files,
            capabilities=("weather.observe",),
            tools=(_tool("observe", "weather.observe"),),
            prompts=({"id": "weather.prompt", "capability": "weather.observe"},),
            surfaces=(
                _surface(
                    "weather.panel",
                    "weather.observe",
                    namespace="weather",
                    key="conditions",
                ),
            ),
        )
        _install(owner, ((unrelated, unrelated_files),), "unrelated")
        other_candidate = _install(owner, (_zero_capability_primary(),), "other-zero-capability")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="other-campaign",
                display_name="Other",
                writer=WriterRole.DM,
                package_lock=other_candidate.package_lock,
                turn_id="other-turn",
            )
        )
        assert owner.catalog_definition_bytes(campaign_id) == before_catalog
        assert owner.package_surface_bytes(campaign_id) == before_surfaces
    print("F3 UNRELATED INVARIANCE", before_catalog.hex(), before_surfaces.hex())


def test_f3_activation_rejects_existing_work_and_incomplete_turn_without_queueing(
    tmp_path: Path,
) -> None:
    owner, old, new, _, _ = _open_versioned(tmp_path)
    try:
        campaign_id = _campaign(owner, old)
        started = threading.Event()
        release = threading.Event()
        owner.start_narration_attempt(
            campaign_id=campaign_id,
            attempt_id="active-narration",
            turn_id="turn-f3-activation",
            cancel=lambda: None,
        )
        started.set()
        assert started.is_set()
        with pytest.raises(CommandRejected, match="work"):
            owner.activate_package(_activation(owner, campaign_id, new), _migrate)
        owner.complete_narration_attempt(campaign_id, "active-narration")
        with pytest.raises(CommandRejected, match="completed TURN"):
            owner.activate_package(_activation(owner, campaign_id, new), _migrate)
        _complete(owner, campaign_id)
        result = owner.activate_package(_activation(owner, campaign_id, new), _migrate)
        release.set()
        assert result.resulting_revision == 1
    finally:
        owner.close()
    print("F3 NONQUEUED", "active work rejected", "checkpoint rejected", result.new_lock_id)


def test_f3_transition_reservation_rejects_new_action_narration_retry_and_catalog_admission(
    tmp_path: Path,
) -> None:
    owner, old, new, _, _ = _open_versioned(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    result: list[object] = []
    try:
        campaign_id = _campaign(owner, old)
        _complete(owner, campaign_id)

        def paused_migration(state: Mapping[str, object]) -> Mapping[str, object]:
            entered.set()
            assert release.wait(timeout=5)
            return _migrate(state)

        thread = threading.Thread(
            target=lambda: result.append(
                owner.activate_package(_activation(owner, campaign_id, new), paused_migration)
            )
        )
        thread.start()
        assert entered.wait(timeout=5)
        with pytest.raises(CommandRejected, match="transition"):
            owner.execute_action(
                ActionCommand(
                    campaign_id=campaign_id,
                    action_id="blocked-action",
                    idempotency_key="blocked-key",
                    owner_id="owner-f3",
                    writer=WriterRole.DM,
                    expected_revision=0,
                    turn_id="turn-f3-activation",
                    package_id="synthetic.clockwork",
                    tool_name="advance",
                    parameters={"steps": 1},
                )
            )
        with pytest.raises(CommandRejected, match="transition"):
            owner.start_narration_attempt(
                campaign_id=campaign_id,
                attempt_id="blocked-narration",
                turn_id="turn-f3-activation",
                cancel=lambda: None,
            )
        with pytest.raises(CommandRejected, match="transition"):
            owner.manual_retry(
                campaign_id=campaign_id,
                interruption_id="blocked-interruption",
                retry_id="blocked-retry",
                narrator=lambda _input, _stream: None,
            )
        with pytest.raises(CommandRejected, match="transition"):
            owner.catalog(campaign_id)
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(result) == 1
    finally:
        release.set()
        owner.close()
    print("F3 BIDIRECTIONAL GATE", "action narration retry catalog rejected", result[0])


def test_f3_failed_migration_precommit_and_confirmed_rollback_preserve_old_authority_across_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "authority.db"
    owner, old, new, _, _ = _open_versioned(tmp_path)
    campaign_id = _campaign(owner, old, state=4)
    _complete(owner, campaign_id)
    before = owner.snapshot(campaign_id)
    old_lock = owner.current_package_lock(campaign_id)
    with pytest.raises(RuntimeError, match="migration"):
        owner.activate_package(
            _activation(owner, campaign_id, new, activation_id="migration-fail"),
            lambda _state: (_ for _ in ()).throw(RuntimeError("migration failed")),
        )
    monkeypatch.setattr(
        owner._store,
        "_before_activation_commit",
        lambda: (_ for _ in ()).throw(RuntimeError("precommit failed")),
    )
    with pytest.raises(RuntimeError, match="precommit"):
        owner.activate_package(
            _activation(owner, campaign_id, new, activation_id="precommit-fail"), _migrate
        )
    assert owner.snapshot(campaign_id) == before
    assert owner.current_package_lock(campaign_id) == old_lock
    owner.close()
    with AuthorityOwner.open(db, "owner-reload") as reopened:
        assert reopened.snapshot(campaign_id) == before
        assert reopened.current_package_lock(campaign_id) == old_lock
    print("F3 ROLLBACK", old_lock.lock_id, before.revision, "reload exact")


def test_f3_commit_exception_resolves_confirmed_rollback_durable_success_and_incoherence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback_path = tmp_path / "rollback"
    rollback_path.mkdir()
    rollback_owner, rollback_old, rollback_new, _, _ = _open_versioned(rollback_path)
    rollback_campaign = _campaign(rollback_owner, rollback_old, state=6)
    _complete(rollback_owner, rollback_campaign)
    rollback_before = rollback_owner.snapshot(rollback_campaign)
    rollback_lock = rollback_owner.current_package_lock(rollback_campaign)
    rollback_command = _activation(
        rollback_owner,
        rollback_campaign,
        rollback_new,
        activation_id="commit-before-durable",
    )
    rollback_connect = rollback_owner._store._connect
    with monkeypatch.context() as scoped:
        scoped.setattr(
            rollback_owner._store,
            "_connect",
            lambda: _ActivationCommitProxy(
                rollback_connect(),
                activation_id=rollback_command.activation_id,
                mode="rollback",
                campaign_id=rollback_campaign,
            ),
        )
        with pytest.raises(CommandRejected) as rolled_back:
            rollback_owner.activate_package(rollback_command, _migrate)
    assert rolled_back.value.code == "activation_commit_rolled_back"
    assert "sqlite" not in str(rolled_back.value).casefold()
    assert "transport detail" not in str(rolled_back.value).casefold()
    assert rollback_owner.snapshot(rollback_campaign) == rollback_before
    assert rollback_owner.current_package_lock(rollback_campaign) == rollback_lock
    connection = rollback_owner._store._connect()
    try:
        assert (
            connection.execute(
                "SELECT 1 FROM package_activations WHERE activation_id = ?",
                (rollback_command.activation_id,),
            ).fetchone()
            is None
        )
    finally:
        connection.close()
    rollback_owner.close()
    with AuthorityOwner.open(rollback_path / "authority.db", "rollback-reloaded") as reopened:
        assert reopened.snapshot(rollback_campaign) == rollback_before
        assert reopened.current_package_lock(rollback_campaign) == rollback_lock

    durable_path = tmp_path / "durable"
    durable_path.mkdir()
    durable_owner, durable_old, durable_new, _, _ = _open_versioned(durable_path)
    durable_campaign = _campaign(durable_owner, durable_old, state=7)
    _complete(durable_owner, durable_campaign)
    durable_command = _activation(
        durable_owner,
        durable_campaign,
        durable_new,
        activation_id="commit-durable-lost-ack",
    )
    durable_connect = durable_owner._store._connect
    durable_calls: list[int] = []

    def durable_migration(state: Mapping[str, object]) -> Mapping[str, object]:
        durable_calls.append(1)
        return _migrate(state)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            durable_owner._store,
            "_connect",
            lambda: _ActivationCommitProxy(
                durable_connect(),
                activation_id=durable_command.activation_id,
                mode="durable",
                campaign_id=durable_campaign,
            ),
        )
        durable_result = durable_owner.activate_package(durable_command, durable_migration)
    assert durable_result.resolution == "durable_commit_resolved"
    assert durable_result.resulting_revision == 1
    assert durable_calls == [1]
    assert durable_owner.snapshot(durable_campaign).revision == 1
    durable_owner.close()

    incoherent_path = tmp_path / "incoherent"
    incoherent_path.mkdir()
    incoherent_owner, incoherent_old, incoherent_new, _, _ = _open_versioned(incoherent_path)
    incoherent_campaign = _campaign(incoherent_owner, incoherent_old, state=8)
    _complete(incoherent_owner, incoherent_campaign)
    incoherent_command = _activation(
        incoherent_owner,
        incoherent_campaign,
        incoherent_new,
        activation_id="commit-incoherent",
    )
    incoherent_connect = incoherent_owner._store._connect
    with monkeypatch.context() as scoped:
        scoped.setattr(
            incoherent_owner._store,
            "_connect",
            lambda: _ActivationCommitProxy(
                incoherent_connect(),
                activation_id=incoherent_command.activation_id,
                mode="incoherent",
                campaign_id=incoherent_campaign,
            ),
        )
        with pytest.raises(CommandRejected) as uncertain:
            incoherent_owner.activate_package(incoherent_command, _migrate)
    assert uncertain.value.code == "activation_commit_uncertain"
    assert "transport detail" not in str(uncertain.value).casefold()
    connection = incoherent_owner._store._connect()
    try:
        assert (
            connection.execute(
                "SELECT 1 FROM package_activations WHERE activation_id = ?",
                (incoherent_command.activation_id,),
            ).fetchone()
            is None
        )
    finally:
        connection.close()
    incoherent_owner.close()
    print(
        "F3 COMMIT OUTCOMES",
        rolled_back.value.code,
        durable_result.resolution,
        uncertain.value.code,
    )


def test_f3_successful_activation_switches_state_lock_lineage_catalog_and_revision_atomically(
    tmp_path: Path,
) -> None:
    owner, old, new, _, _ = _open_versioned(tmp_path)
    try:
        campaign_id = _campaign(owner, old, state=2)
        old_lock = owner.current_package_lock(campaign_id)
        _complete(owner, campaign_id)
        result = owner.activate_package(_activation(owner, campaign_id, new), _migrate)
        snapshot = owner.snapshot(campaign_id)
        current = owner.current_package_lock(campaign_id)
        lineage = owner.package_lock_lineage(campaign_id)
        assert result.status is ActivationStatus.COMMITTED
        assert result.old_lock_id == old_lock.lock_id
        assert current.lock_id == result.new_lock_id
        assert current.predecessor_lock_id == old_lock.lock_id
        assert snapshot.revision == 1
        assert snapshot.state["clockwork.counter"] == 102
        assert [(item.predecessor_lock_id, item.lock_id) for item in lineage] == [
            (None, old_lock.lock_id),
            (old_lock.lock_id, current.lock_id),
        ]
        assert owner.catalog(campaign_id)[0].package_version == "2"
        connection = owner._store._connect()
        try:
            state_owner = connection.execute(
                "SELECT namespace, owner_package_id "
                "FROM campaign_state_namespace_owners WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
        finally:
            connection.close()
        assert [tuple(row) for row in state_owner] == [("clockwork", "synthetic.clockwork")]
    finally:
        owner.close()
    print(
        "F3 ATOMIC ACTIVATION",
        result.activation_id,
        result.old_lock_id,
        result.new_lock_id,
        result.resulting_revision,
        lineage,
    )


def test_f3_activation_identity_resolves_duplicate_collision_lost_ack_and_uncertain_commit_without_repeat(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, old, new, _, _ = _open_versioned(tmp_path)
    calls: list[int] = []
    try:
        campaign_id = _campaign(owner, old, state=1)
        _complete(owner, campaign_id)
        command = _activation(owner, campaign_id, new, activation_id="stable-activation")

        def migration(state: Mapping[str, object]) -> Mapping[str, object]:
            calls.append(1)
            return _migrate(state)

        monkeypatch.setattr(
            owner._store,
            "_after_activation_commit",
            lambda: (_ for _ in ()).throw(RuntimeError("lost acknowledgement")),
        )
        first = owner.activate_package(command, migration)
        assert first.resolution == "durable_commit_resolved"
        monkeypatch.setattr(owner._store, "_after_activation_commit", lambda: None)
        duplicate = owner.activate_package(command, migration)
        assert duplicate.replayed is True
        assert duplicate.new_lock_id == first.new_lock_id
        assert calls == [1]
        collision = ActivationCommand(
            campaign_id=command.campaign_id,
            candidate_id=command.candidate_id,
            activation_id=command.activation_id,
            expected_revision=999,
            expected_lock_id=command.expected_lock_id,
            migration_id=command.migration_id,
        )
        with pytest.raises(IdempotencyConflict):
            owner.activate_package(collision, migration)
        assert owner.snapshot(campaign_id).revision == 1
        connection = owner._store._connect()
        try:
            row = connection.execute(
                "SELECT status, resulting_revision FROM package_activations "
                "WHERE activation_id = ?",
                (command.activation_id,),
            ).fetchone()
            assert tuple(row) == ("committed", 1)
        finally:
            connection.close()
    finally:
        owner.close()
    print(
        "F3 ACTIVATION IDEMPOTENCY",
        first.activation_id,
        first.resolution,
        duplicate.replayed,
        "migration calls",
        calls,
    )


def test_f3_activation_identity_lookup_precedes_candidate_revision_and_closure_admission(
    tmp_path: Path,
) -> None:
    owner, old, new, _, _ = _open_versioned(tmp_path)
    migration_calls: list[int] = []
    try:
        campaign_id = _campaign(owner, old, state=9)
        _complete(owner, campaign_id)
        command = _activation(owner, campaign_id, new, activation_id="lookup-first")

        def migration(state: Mapping[str, object]) -> Mapping[str, object]:
            migration_calls.append(1)
            return _migrate(state)

        first = owner.activate_package(command, migration)
        duplicate = owner.activate_package(command, migration)
        assert duplicate.replayed is True
        assert duplicate.new_lock_id == first.new_lock_id
        assert migration_calls == [1]
        assert owner.snapshot(campaign_id).revision == 1

        missing_collision = ActivationCommand(
            campaign_id=command.campaign_id,
            candidate_id="candidate-does-not-exist",
            activation_id=command.activation_id,
            expected_revision=command.expected_revision,
            expected_lock_id=command.expected_lock_id,
            migration_id=command.migration_id,
        )
        with pytest.raises(IdempotencyConflict):
            owner.activate_package(missing_collision, migration)

        new_missing = ActivationCommand(
            campaign_id=command.campaign_id,
            candidate_id="candidate-does-not-exist",
            activation_id="new-missing-activation",
            expected_revision=1,
            expected_lock_id=first.new_lock_id,
            migration_id=command.migration_id,
        )
        with pytest.raises(CommandRejected) as unavailable:
            owner.activate_package(new_missing, migration)
        assert unavailable.value.code == "candidate_unavailable"
        assert migration_calls == [1]
        assert owner.snapshot(campaign_id).revision == 1
    finally:
        owner.close()
    print(
        "F3 LOOKUP ORDER",
        first.activation_id,
        duplicate.resolution,
        unavailable.value.code,
        migration_calls,
    )


def test_f3_startup_rejects_each_strict_canonical_activation_result_disagreement(
    tmp_path: Path,
) -> None:
    corruptions: dict[str, object] = {
        "old_lock_id": "wrong-old-lock",
        "new_lock_id": "wrong-new-lock",
        "resulting_revision": 999,
        "status": "failed",
        "campaign_id": "wrong-campaign",
        "activation_id": "wrong-activation",
        "payload_sha256": "0" * 64,
    }
    rejected: list[str] = []
    for field, wrong_value in corruptions.items():
        case = tmp_path / field
        case.mkdir()
        owner, old, new, _, _ = _open_versioned(case)
        campaign_id = _campaign(owner, old, state=10)
        _complete(owner, campaign_id)
        command = _activation(owner, campaign_id, new, activation_id=f"result-{field}")
        owner.activate_package(command, _migrate)
        owner.close()

        db = case / "authority.db"
        connection = sqlite3.connect(db)
        try:
            row = connection.execute(
                "SELECT result_json FROM package_activations WHERE activation_id = ?",
                (command.activation_id,),
            ).fetchone()
            assert row is not None
            report = json.loads(str(row[0]))
            report[field] = wrong_value
            connection.execute(
                "UPDATE package_activations SET result_json = ? WHERE activation_id = ?",
                (
                    json.dumps(report, sort_keys=True, separators=(",", ":")),
                    command.activation_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(SchemaIdentityError), AuthorityOwner.open(db, f"reopen-{field}"):
            pass
        rejected.append(field)
    assert rejected == list(corruptions)
    print("F3 ACTIVATION RESULT AGREEMENT", tuple(rejected))


def test_f3_readable_install_activation_recovery_and_reclamation_probe(tmp_path: Path) -> None:
    db = tmp_path / "authority.db"
    owner, old, new, old_record, new_record = _open_versioned(tmp_path)
    campaign_id = _campaign(owner, old, state=3)
    old_lock = owner.current_package_lock(campaign_id)
    candidate_order = [entry.package_id for entry in new.package_lock.entries]
    candidate_hashes = [entry.sha256 for entry in new.package_lock.entries]
    _complete(owner, campaign_id)
    result = owner.activate_package(
        _activation(owner, campaign_id, new, activation_id="readable-activation"), _migrate
    )
    current = owner.current_package_lock(campaign_id)
    catalog = owner.catalog(campaign_id)
    surfaces = owner.package_surfaces(campaign_id)
    roots = owner.reclaim_package_bytes()
    snapshot = owner.snapshot(campaign_id)
    lineage = owner.package_lock_lineage(campaign_id)
    owner.close()
    with AuthorityOwner.open(db, "owner-recovered") as recovered:
        recovered_lock = recovered.current_package_lock(campaign_id)
        recovered_snapshot = recovered.snapshot(campaign_id)
        assert recovered_lock == current
        assert recovered_snapshot == snapshot
    print(
        "F3 READABLE",
        {
            "candidate": new.candidate_id,
            "candidate_status": new.status.value,
            "old_lock": old_lock.lock_id,
            "current_lock": current.lock_id,
            "closure_order": candidate_order,
            "hashes": candidate_hashes,
            "catalog": [(item.name, item.input_schema) for item in catalog],
            "surfaces": surfaces,
            "activation": (result.activation_id, result.resolution, result.resulting_revision),
            "migration_state": snapshot.state,
            "lineage": lineage,
            "roots": roots,
            "reclaimed": roots.deleted_blobs,
            "reload": recovered_lock.lock_id,
            "manifests": (old_record["content_sha256"], new_record["content_sha256"]),
        },
    )
