"""Packages own starting-state constraints; Plan admission enforces them before play."""

import io
import json
from copy import deepcopy
from dataclasses import replace

import pytest
import yaml

from sylvia_gm.agent._builder_session import _campaign_plan_example
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.application.plan_snapshot import validate_plan_snapshot
from sylvia_gm.application.plan_state_admission import validate_plan_state
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.builder._package_record_edits import apply_package_record_edits
from sylvia_gm.builder._package_snapshots import retained_package_drafts
from sylvia_gm.builder._plan_snapshot_materializer import (
    _rebind_plan_to_snapshot,
    materialize_plan_snapshot_draft,
)
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.builder._setting_materializer import _world_draft, materialize_setting_draft
from sylvia_gm.domain.authority import PackageRole, thaw_json_value
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.starting_state import (
    REQUIRED_STARTING_STATE_FEATURE,
    STARTING_STATE_FEATURE,
    parse_state_schemas,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_panel_starting_layout import compile_layout, starting_layout


def declaration():
    return {
        "key": "state", "schema": {"type": "object", "properties": {
            "private_cost": {"type": "integer", "minimum": 0},
            "nodes": {"type": "object"}, "operations": {"type": "object"},
        }}, "references": [{
            "source": ["operations", "$node", "*"], "target_key": "state",
            "target": ["nodes", "$node", "*", "$key"],
        }],
    }


@pytest.fixture
def manifest():
    compiled = compile_layout(starting_layout())
    record = deepcopy(compiled.draft.record)
    record["state_schemas"] = [declaration()]
    record["compatibility"]["requires"].append(STARTING_STATE_FEATURE)
    with (
        materialize_package_archive((replace(compiled.draft, record=record),)) as package,
        admit_package_archive(package.archive) as admitted,
    ):
        return admitted.manifests[0]


def plan(manifest, **state):
    return {"initial_state": {"mechanics": [{
        "namespace": manifest.package_id, "key": "state", "value": state,
    }]}}


def test_valid_private_state_is_checked_without_mutation_or_invented_defaults(manifest):
    payload = plan(manifest, private_cost=3, nodes={"a": {"open": {}}}, operations={"a": ["open"]})
    before = deepcopy(payload)
    validate_plan_state(payload, manifest)
    assert payload == before
    validate_plan_state({"initial_state": {"mechanics": []}}, manifest)
    validate_plan_state(plan(manifest), manifest)
    # A different namespace is never used to satisfy a package-owned reference.
    payload["initial_state"]["mechanics"].append({
        "namespace": "someone.else", "key": "state", "value": {"nodes": {"a": {"secret": {}}}},
    })
    payload["initial_state"]["mechanics"][0]["value"]["operations"] = {"a": ["secret"]}
    with pytest.raises(ContentShapeError):
        validate_plan_state(payload, manifest)


def test_required_starting_state_cannot_be_omitted_or_supplied_by_another_namespace(manifest):
    manifest = replace(manifest, state_schemas=(replace(manifest.state_schemas[0], required=True),))
    for payload in ({"initial_state": {}}, {"initial_state": {"mechanics": [{
        "namespace": "some.other.rules", "key": "state", "value": {},
    }]}}):
        before = deepcopy(payload)
        with pytest.raises(ContentShapeError) as failure:
            validate_plan_state(payload, manifest)
        assert failure.value.diagnostics[0].path == "$.initial_state.mechanics"
        assert manifest.package_id in failure.value.diagnostics[0].message
        assert payload == before
    validate_plan_state(plan(manifest, private_cost=1), manifest)


@pytest.fixture
def required_state_draft():
    """Supply explicit full-record host support independently of compact inference."""
    compiled = compile_layout(starting_layout())
    record = deepcopy(compiled.draft.record)
    record["state_schemas"] = [{**declaration(), "required": True}]
    record["compatibility"]["requires"] = sorted(
        {
            *record["compatibility"]["requires"],
            STARTING_STATE_FEATURE,
            REQUIRED_STARTING_STATE_FEATURE,
        }
    )
    return replace(compiled.draft, record=record)


@pytest.mark.parametrize("change", ["remove", "optional", "weaken", "replace_source"])
def test_owned_record_edits_preserve_sources_and_enforce_complete_identity(
    tmp_path,
    required_state_draft,
    change,
):
    original = required_state_draft
    before = deepcopy(original)
    with materialize_package_archive((original,)) as package:
        original_bytes = package.archive.read()
        original_identity = package.manifest.identity
        assert package.manifest.state_schemas[0].required
    record = deepcopy(original.record)
    if change == "remove":
        record.pop("state_schemas")
    elif change == "optional":
        record["state_schemas"][0]["required"] = False
    elif change == "weaken":
        record["state_schemas"][0]["schema"] = {"type": "object"}
    else:
        record["id"] = "example.other"
    root = tmp_path / "draft"
    records = root / "package-records"
    records.mkdir(parents=True)
    (records / f"{original.record['id']}.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )
    if change in {"remove", "replace_source"}:
        message = (
            "Builder package record fields do not match the saved package"
            if change == "remove"
            else "Builder package record cannot change id"
        )
        with pytest.raises(CommandRejected) as failure:
            apply_package_record_edits(tmp_path, root, (original,))
        assert failure.value.code == "command_rejected"
        assert str(failure.value) == message
    else:
        edited = apply_package_record_edits(tmp_path, root, (original,))
        assert len(edited) == 1
        assert edited[0].record == record
        assert edited[0].payloads == original.payloads
        assert edited[0].format_version == 4
        with (
            materialize_package_archive(edited) as package,
            admit_package_archive(package.archive) as admitted,
        ):
            copied = admitted.manifests[0]
            assert copied.identity[:2] == (original.record["id"], original.record["version"])
            assert copied.identity != original_identity
            assert copied.state_schemas[0].required is (change == "weaken")
            assert (
                thaw_json_value(copied.state_schemas[0].schema)
                == record["state_schemas"][0]["schema"]
            )
            assert REQUIRED_STARTING_STATE_FEATURE in copied.compatibility["requires"]
            validate_plan_state(plan(copied, private_cost=1), copied)
            if change == "optional":
                validate_plan_state({"initial_state": {}}, copied)
                with pytest.raises(ContentShapeError):
                    validate_plan_state(plan(copied, private_cost=-1), copied)
            else:
                validate_plan_state(plan(copied, private_cost=-1), copied)
                with pytest.raises(ContentShapeError):
                    validate_plan_state({"initial_state": {}}, copied)
    assert original == before
    with materialize_package_archive((original,)) as package:
        assert package.archive.read() == original_bytes


def test_required_state_host_contract_is_derived_and_enforced():
    source = {
        "format": 1, "id": "example.required", "version": "1", "label": "Required fields",
        "publisher": "Example", "mechanic_families": {}, "public_actions": [],
        "private_mechanics": [], "definitions": {}, "participant_artifacts": [],
        "operating_guide": None, "state_schemas": [{**declaration(), "required": True}],
    }
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    assert REQUIRED_STARTING_STATE_FEATURE in compiled.required_engine_features
    with materialize_package_archive((compiled.draft,)) as archive:
        assert archive.manifest.state_schemas[0].required
    record = deepcopy(compiled.draft.record)
    record["compatibility"]["requires"].remove(REQUIRED_STARTING_STATE_FEATURE)
    with pytest.raises((ValueError, CommandRejected)):
        materialize_package_archive((replace(compiled.draft, record=record),))


@pytest.mark.parametrize("target", ["setting", "plan"])
def test_selected_source_record_removal_rejects_before_publication(
    tmp_path,
    required_state_draft,
    target,
):
    with AuthorityOwner.open(tmp_path / "source.sqlite3", "source-owner") as owner:
        ruleset = compile_layout(starting_layout()).draft
        world = _world_draft(
            package_id="example.source-world",
            version="1",
            title="Source world",
            payloads={"overview.md": b"# Synthetic source world"},
            ruleset=ruleset,
        )
        with materialize_package_archive((ruleset, world)) as package:
            original = owner.install_package_archive(package.archive, install_id="original-source")
        record = deepcopy(required_state_draft.record)
        record["version"] = "2.0.0"
        with materialize_package_archive(
            (replace(required_state_draft, record=record),)
        ) as package:
            selected = owner.install_package_archive(package.archive, install_id="required-source")
        sources_before = owner.package_candidates()
        archives_before = {}
        for candidate in (original, selected):
            with io.BytesIO() as exported:
                owner.export_package_candidate(candidate.candidate_id, exported)
                archives_before[candidate.candidate_id] = exported.getvalue()

        def assert_sources_unchanged():
            assert owner.package_candidates() == sources_before
            for candidate in (original, selected):
                assert owner.package_candidate(candidate.candidate_id) == candidate
                with io.BytesIO() as exported:
                    owner.export_package_candidate(candidate.candidate_id, exported)
                    assert exported.getvalue() == archives_before[candidate.candidate_id]

        authority = campaign_plan_sources(owner, original.package_lock)
        payload = json.loads(_campaign_plan_example(authority))
        payload["initial_state"]["mechanics"] = plan(
            authority.ruleset,
            private_cost=1,
        )["initial_state"]["mechanics"]
        readable = "# Synthetic Plan\n\nExplore the source world."
        authority.validate(payload, readable)
        workspace = tmp_path / "edit"
        root = workspace / ("setting-draft" if target == "setting" else "campaign-plan-draft")
        (root / "package-records").mkdir(parents=True)
        (root / "ruleset-source.json").write_text(
            json.dumps(
                {
                    "candidate_id": selected.candidate_id,
                    "expected_closure_sha256": selected.closure_sha256,
                }
            ),
            encoding="utf-8",
        )
        incomplete = deepcopy(record)
        incomplete.pop("state_schemas")
        record_path = root / "package-records" / f"{record['id']}.json"
        record_path.write_text(
            json.dumps(incomplete),
            encoding="utf-8",
        )
        if target == "setting":
            (root / "files").mkdir()
            (root / "files" / "overview.md").write_text("# Synthetic world", encoding="utf-8")
        else:
            (root / "plan.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            (root / "plan.md").write_text(readable, encoding="utf-8")

        def materialize():
            if target == "setting":
                return materialize_setting_draft(
                    owner,
                    workspace,
                    candidate_id=original.candidate_id,
                    package_id="example.world",
                    version="1",
                    title="World",
                )
            return materialize_plan_snapshot_draft(
                owner,
                workspace,
                candidate_id=original.candidate_id,
                expected_authority=authority,
            )

        with pytest.raises(CommandRejected) as failure:
            materialize()
        assert failure.value.code == "command_rejected"
        assert str(failure.value) == "Builder package record fields do not match the saved package"
        assert_sources_unchanged()
        # Restoring the complete record reaches admission of the selected required copy.
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with materialize() as package, admit_package_archive(package.archive) as admitted:
            primary = next(item for item in admitted.manifests if item.role is PackageRole.PRIMARY)
            assert primary.package_id == record["id"] and primary.version == "2.0.0"
            assert primary.state_schemas[0].required
            assert REQUIRED_STARTING_STATE_FEATURE in primary.compatibility["requires"]
        assert_sources_unchanged()


@pytest.mark.parametrize("state, suffix", [
    ({"private_cost": "NEVER_ECHO_THIS"}, ".private_cost"),
    ({"private_cost": -1}, ".private_cost"),
    ({"nodes": {"other": {"open": {}}}, "operations": {"a": ["open"]}}, ".operations.a[0]"),
    ({"operations": {"a": ["NEVER_ECHO_THIS"]}}, ".operations.a[0]"),
])
def test_invalid_private_state_has_corrective_paths_without_values(manifest, state, suffix):
    payload = plan(manifest, **state)
    before = deepcopy(payload)
    with pytest.raises(ContentShapeError) as failure:
        validate_plan_state(payload, manifest)
    diagnostic = failure.value.diagnostics[0]
    assert diagnostic.path == "$.initial_state.mechanics[0].value" + suffix
    assert diagnostic.code == "plan_starting_state_invalid"
    assert "NEVER_ECHO_THIS" not in str(failure.value)
    assert payload == before


@pytest.mark.parametrize("reference", [
    {"source": [], "target_key": "state", "target": ["nodes"]},
    {"source": ["$node", "$node"], "target_key": "state", "target": ["nodes"]},
    {"source": ["*"], "target_key": "state", "target": ["$unbound"]},
    {"source": ["$key", "id"], "target_key": "state", "target": ["nodes"]},
    {"source": ["*"], "target_key": "state", "target": ["nodes"], "namespace": "other"},
])
def test_malformed_reference_declarations_are_rejected(reference):
    item = declaration()
    item["references"] = [reference]
    with pytest.raises(ValueError):
        parse_state_schemas([item])


@pytest.mark.parametrize("fault", ["missing_host_support", "unsupported_schema"])
def test_package_admission_rejects_unenforceable_declarations(fault):
    compiled = compile_layout(starting_layout())
    record = deepcopy(compiled.draft.record)
    record["state_schemas"] = [declaration()]
    if fault == "unsupported_schema":
        record["compatibility"]["requires"].append(STARTING_STATE_FEATURE)
        record["state_schemas"][0]["schema"]["$ref"] = "https://invalid.example/schema"
    with (
        pytest.raises((ValueError, CommandRejected)),
        materialize_package_archive((replace(compiled.draft, record=record),)) as package,
        admit_package_archive(package.archive),
    ):
        pass


def test_normal_plan_snapshot_checks_its_exact_retained_package(tmp_path):
    with AuthorityOwner.open(tmp_path / "state.sqlite3", "plan-state-owner") as owner:
        candidate, _setting, saved, _characters = seed_campaign_library(owner, tmp_path)
        drafts = list(retained_package_drafts(owner, candidate.candidate_id, include_setting=True))
        for index, draft in enumerate(drafts):
            if draft.record["role"] != PackageRole.PRIMARY.value:
                continue
            record = thaw_json_value(draft.record)
            record["state_schemas"] = [declaration()]
            record["compatibility"]["requires"].append(STARTING_STATE_FEATURE)
            drafts[index] = replace(draft, record=record)
        with materialize_package_archive(tuple(drafts)) as package:
            snapshot = package.archive.read()
        # Same archive admission used for portable imports; no installation is needed.
        with admit_package_archive(io.BytesIO(snapshot)):
            pass
        payload = _rebind_plan_to_snapshot(snapshot, thaw_json_value(saved.plan.payload))
        state = payload["initial_state"]["mechanics"][0]["value"]
        state["private_cost"] = 2
        validate_plan_snapshot(snapshot, payload, saved.plan.readable_markdown)
        state["private_cost"] = -2
        with pytest.raises(ContentShapeError) as failure:
            validate_plan_snapshot(snapshot, payload, saved.plan.readable_markdown)
        assert failure.value.diagnostics[0].path.endswith(".private_cost")


@pytest.mark.parametrize("authoring_format", ["compact", "package"])
def test_builder_preserves_starting_state_checks_and_host_requirement(tmp_path, authoring_format):
    source = {
        "format": 1, "id": "example.state-checks", "version": "1", "label": "State checks",
        "publisher": "Example", "mechanic_families": {}, "public_actions": [],
        "private_mechanics": [], "definitions": {}, "participant_artifacts": [],
        "operating_guide": None, "state_schemas": [declaration()],
    }
    encoded = yaml.safe_dump(source).encode()
    compiled = compile_ruleset_source(encoded)
    assert compiled.required_engine_features == (STARTING_STATE_FEATURE,)
    root = tmp_path / "ruleset-draft"
    root.mkdir()
    if authoring_format == "compact":
        (root / "ruleset-source.yaml").write_bytes(encoded)
    else:
        from sylvia_gm.domain.package_library import canonical_json_bytes

        record = deepcopy(compiled.draft.record)
        del record["content_kind"]
        (root / "package.json").write_bytes(canonical_json_bytes(record))
        for name, value in compiled.draft.payloads.items():
            path = root / "files" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
    with (
        materialize_ruleset_draft(tmp_path) as materialized,
        admit_package_archive(materialized.archive) as admitted,
    ):
        manifest = admitted.manifests[0]
        assert len(manifest.state_schemas) == 1
        with pytest.raises(ContentShapeError):
            validate_plan_state(plan(manifest, private_cost=-3), manifest)
