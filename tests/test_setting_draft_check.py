from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.builder._setting_draft_check import check_setting_draft
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_character_creation_contract import _draft

ARGS = {
    "package_id": "example.glass-harbor",
    "version": "1.0.0",
    "title": "Glass Harbor",
}


def _source_candidate(owner: AuthorityOwner, workspace: Path):
    _draft(workspace)
    with materialize_ruleset_draft(workspace) as materialized:
        return owner.install_package_archive(
            BytesIO(materialized.archive.read()), install_id="source-rules"
        )


def _setting_files(workspace: Path, requirements: object) -> None:
    files = workspace / "setting-draft" / "files"
    files.mkdir(parents=True)
    (files / "world.md").write_text("# Glass Harbor\n", encoding="utf-8")
    (files / "character-requirements.json").write_bytes(canonical_json_bytes(requirements))


def _requirements(*, version: object = 1, field_type: str = "string") -> dict[str, object]:
    return {
        "format_id": "sylvia-gm-setting-character-requirements",
        "format_version": version,
        "schema": {
            "type": "object",
            "properties": {"home": {"type": field_type}},
            "required": ["home"],
            "additionalProperties": False,
        },
    }


def _workspace_bytes(workspace: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }


def test_valid_setting_check_preserves_workspace_and_candidate_catalog(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = tmp_path / "owner.sqlite3"
    with AuthorityOwner.open(database, "setting-check") as owner:
        source = _source_candidate(owner, workspace)
        _setting_files(workspace, _requirements())
        before_files = _workspace_bytes(workspace)
        before_candidates = owner.package_candidates()

        result = check_setting_draft(owner, workspace, candidate_id=source.candidate_id, **ARGS)

        assert result["closure_sha256"]
        assert result["published"] is False
        assert result["state_changed"] is False
        assert "contained_ruleset" in result["checked"]
        assert owner.package_candidates() == before_candidates
        assert _workspace_bytes(workspace) == before_files


@pytest.mark.parametrize("failure", ["formatting", "missing_reference"])
def test_setting_knowledge_rejection_names_editable_file_and_recovers(
    tmp_path: Path, failure: str,
) -> None:
    workspace = tmp_path / "workspace"
    knowledge = {
        "format_id": "sylvia-gm-campaign-setting-knowledge",
        "format_version": 1, "facts": [], "private_references": [],
    }
    with AuthorityOwner.open(tmp_path / "owner.sqlite3", "knowledge-check") as owner:
        source = _source_candidate(owner, workspace)
        _setting_files(workspace, _requirements())
        path = workspace / "setting-draft" / "files" / "setting-knowledge.json"
        broken = {**knowledge, "private_references": ["missing.md"]}
        path.write_bytes(
            json.dumps(knowledge, indent=2).encode("utf-8")
            if failure == "formatting" else canonical_json_bytes(broken)
        )
        before = _workspace_bytes(workspace)
        with pytest.raises(AuthorityError) as rejected:
            check_setting_draft(owner, workspace, candidate_id=source.candidate_id, **ARGS)
        diagnostic = rejected.value.diagnostics[0]
        assert diagnostic.file == "setting-knowledge.json"
        assert diagnostic.package_id == ARGS["package_id"]
        assert "Campaign Setting knowledge" in diagnostic.message
        assert "sorted keys" in diagnostic.guidance
        assert _workspace_bytes(workspace) == before

        path.write_bytes(canonical_json_bytes(knowledge))
        result = check_setting_draft(owner, workspace, candidate_id=source.candidate_id, **ARGS)
        assert result["published"] is False


@pytest.mark.parametrize(
    "requirements",
    [_requirements(version=2), _requirements(field_type="executable")],
)
def test_bad_requirements_match_install_diagnostics_without_publication(
    tmp_path: Path, requirements: dict[str, object]
) -> None:
    workspace = tmp_path / "workspace"
    database = tmp_path / "owner.sqlite3"
    with AuthorityOwner.open(database, "setting-check-bad") as owner:
        source = _source_candidate(owner, workspace)
        _setting_files(workspace, requirements)
        before_files = _workspace_bytes(workspace)
        before_candidates = owner.package_candidates()

        with pytest.raises(AuthorityError) as checked:
            check_setting_draft(owner, workspace, candidate_id=source.candidate_id, **ARGS)
        with materialize_setting_draft(
            owner, workspace, candidate_id=source.candidate_id, **ARGS
        ) as materialized, pytest.raises(AuthorityError) as installed:
            owner.install_package_archive(materialized.archive, install_id="invalid-setting")

        assert checked.value.code == installed.value.code
        assert str(checked.value) == str(installed.value)
        assert [item.as_payload() for item in checked.value.diagnostics] == [
            item.as_payload() for item in installed.value.diagnostics
        ]
        assert owner.package_candidates() == before_candidates
        assert _workspace_bytes(workspace) == before_files
