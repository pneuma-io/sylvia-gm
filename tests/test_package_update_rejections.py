"""Focused rejection checks for owned package updates."""

import json
from dataclasses import replace

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.builder._package_record_edits import apply_package_record_edits
from sylvia_gm.builder._package_snapshots import ruleset_copy, select_snapshot_sources
from sylvia_gm.domain.errors import CommandRejected
from tests.test_builder_library import install_character_packages


@pytest.fixture
def owned_ruleset(tmp_path):
    with AuthorityOwner.open(tmp_path / "library.db", "test") as owner:
        candidate, _ = install_character_packages(owner)
        copied = ruleset_copy(owner, candidate.candidate_id)
        primary = copied[0]
        support_id = "local.synthetic-support"
        support = replace(
            primary,
            record={
                **primary.record,
                "id": support_id,
                "role": "dependency",
                "content_kind": "support",
                "dependencies": [],
            },
            payloads={},
        )
        primary = replace(
            primary,
            record={
                **primary.record,
                "dependencies": [
                    {
                        "id": support_id,
                        "version": support.record["version"],
                        "namespace": "support",
                        "kind": "reference",
                    }
                ],
            },
        )
        yield owner, candidate, (primary, support)


def _write_record(root, draft, record):
    records = root / "package-records"
    records.mkdir(exist_ok=True)
    (records / f"{draft.record['id']}.json").write_text(json.dumps(record), encoding="utf-8")


def test_rejects_record_that_changes_saved_package_id(tmp_path, owned_ruleset):
    _, _, drafts = owned_ruleset
    primary = next(draft for draft in drafts if draft.record["role"] == "primary")
    replacement = dict(primary.record)
    replacement["id"] = "local.different-ruleset"
    _write_record(tmp_path, primary, replacement)

    with pytest.raises(CommandRejected, match="cannot change id"):
        apply_package_record_edits(tmp_path, tmp_path, drafts)


@pytest.mark.parametrize(
    ("dependency_change", "message"),
    [
        ({"id": "local.missing-dependency"}, "absent from the saved copy"),
        ({"version": "999.0.0"}, "version does not match its supplied draft"),
    ],
)
def test_rejects_dependency_that_does_not_match_saved_closure(
    tmp_path, owned_ruleset, dependency_change, message
):
    _, _, drafts = owned_ruleset
    dependent = next(draft for draft in drafts if draft.record["dependencies"])
    replacement = dict(dependent.record)
    dependencies = [dict(item) for item in replacement["dependencies"]]
    dependencies[0].update(dependency_change)
    replacement["dependencies"] = dependencies
    _write_record(tmp_path, dependent, replacement)

    with pytest.raises(CommandRejected, match=message):
        apply_package_record_edits(tmp_path, tmp_path, drafts)


def test_rejects_stale_exact_source_closure(tmp_path, owned_ruleset):
    owner, candidate, drafts = owned_ruleset
    (tmp_path / "ruleset-source.json").write_text(
        json.dumps(
            {
                "candidate_id": candidate.candidate_id,
                "expected_closure_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CommandRejected, match="Source selection changed"):
        select_snapshot_sources(owner, tmp_path, tmp_path, drafts)
