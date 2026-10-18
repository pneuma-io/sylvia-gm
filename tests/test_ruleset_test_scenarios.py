"""Saved scenarios survive both real authoring paths without certifying execution."""

from __future__ import annotations

import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain._content_schema_ruleset import EXAMPLE as SOURCE
from sylvia_gm.domain._content_schema_ruleset_tests import EXAMPLE, RULESET_TESTS_PATH
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.ruleset_tests import parse_ruleset_tests, validate_ruleset_tests


def _stage(workspace: Path, *, compact: bool, suite: dict) -> Path:
    """Build equivalent draft inputs through the actual compact compiler."""
    draft = workspace / "ruleset-draft"
    draft.mkdir()
    source = {**deepcopy(SOURCE), "tests": suite}
    if compact:
        target = draft / "ruleset-source.yaml"
        target.write_text(yaml.safe_dump(source), encoding="utf-8")
        return target
    compiled = compile_ruleset_source(yaml.safe_dump(SOURCE).encode())
    record = {key: value for key, value in compiled.draft.record.items() if key != "content_kind"}
    (draft / "package.json").write_text(json.dumps(record), encoding="utf-8")
    for name, data in compiled.draft.payloads.items():
        if name == "content/content-declarations.json":
            declarations = json.loads(data)
            declarations["inclusions"][0]["links"]["artifacts"].append(RULESET_TESTS_PATH)
            data = canonical_json_bytes(declarations)
        target = draft / "files" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    target = draft / "files" / RULESET_TESTS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(suite), encoding="utf-8")
    return target


@pytest.mark.parametrize("compact", [True, False])
def test_saved_cases_survive_materialization_and_change_candidate_identity(tmp_path, compact):
    suite = deepcopy(EXAMPLE)
    target = _stage(tmp_path, compact=compact, suite=suite)
    before = target.read_bytes()
    with materialize_ruleset_draft(tmp_path) as candidate:
        first_hash = candidate.closure_sha256
        with zipfile.ZipFile(candidate.archive) as archive:
            names = [name for name in archive.namelist() if name.endswith(RULESET_TESTS_PATH)]
            assert len(names) == 1
            assert parse_ruleset_tests(archive.read(names[0])) == suite
    assert target.read_bytes() == before
    if compact:
        value = yaml.safe_load(before)
        value["tests"]["scenarios"][0]["rolls"][0]["value"] = 1
        target.write_text(yaml.safe_dump(value), encoding="utf-8")
    else:
        suite["scenarios"][0]["rolls"][0]["value"] = 1
        target.write_text(json.dumps(suite), encoding="utf-8")
    with materialize_ruleset_draft(tmp_path) as candidate:
        assert candidate.closure_sha256 != first_hash


@pytest.mark.parametrize("compact", [True, False])
def test_invalid_case_rejects_without_changing_draft(tmp_path, compact):
    suite = deepcopy(EXAMPLE)
    suite["scenarios"][0]["rolls"][0]["value"] = 3
    target = _stage(tmp_path, compact=compact, suite=suite)
    before = target.read_bytes()
    with pytest.raises(CommandRejected) as rejected:
        materialize_ruleset_draft(tmp_path)
    assert rejected.value.diagnostics[0].path.endswith("rolls[0].value")
    assert rejected.value.diagnostics[0].file == (
        "ruleset-source.yaml" if compact else RULESET_TESTS_PATH
    )
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "data",
    [
        b'{"format":1,"format":1,"scenarios":[]}',
        b'{"format":1,"scenarios":[],"value":NaN}',
    ],
)
def test_ambiguous_or_nonfinite_json_rejects(data):
    with pytest.raises(ContentShapeError):
        parse_ruleset_tests(data)


def test_duplicate_cases_and_empty_assertions_do_not_count_as_valid_tests():
    suite = deepcopy(EXAMPLE)
    suite["scenarios"].append(deepcopy(suite["scenarios"][0]))
    with pytest.raises(ContentShapeError, match="Content fields"):
        validate_ruleset_tests(suite)
    suite = deepcopy(EXAMPLE)
    suite["scenarios"][0]["steps"][0]["expect"] = []
    with pytest.raises(ContentShapeError):
        validate_ruleset_tests(suite)
