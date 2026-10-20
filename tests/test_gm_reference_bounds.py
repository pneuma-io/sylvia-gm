"""Actual admitted Classes references remain discoverable without unbounded model replies."""

import io
import json
from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.authority import StateValue
from sylvia_gm.domain.campaign_plan import CAMPAIGN_PLAN_FORMAT_VERSION
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes


def _start_reference_campaign(owner, candidate, state, package_id):
    confirmation = owner.confirm_new_campaign(
        operation_id="confirm",
        display_name="Reference QA",
        package_lock=candidate.package_lock,
        turn_id="construction",
        package_candidate_id=candidate.candidate_id,
        builder_session_id="builder",
        builder_provider_id="fixture",
        builder_model_id="fixture",
        participants=(),
        initial_state=(StateValue(package_id, "state", state),),
    )
    cid = confirmation.campaign_id
    owner.begin_campaign_construction(
        operation_id="begin",
        campaign_id=cid,
        lane="quick_start",
        expected_revision=0,
        canonical_input={"premise": "Reference QA"},
    )
    lock = owner.current_package_lock(cid)
    primary = next(p for p in lock.package_lock.entries if p.package_id == package_id)
    owner.finalize_campaign_construction(
        campaign_id=cid,
        dm_provider_id="fixture",
        dm_model_id="fixture",
        plan_artifact_text="Reference QA",
        model_guidance_override=True,
        structured_plan={
            "format_id": "sylvia-gm-campaign-plan",
            "format_version": CAMPAIGN_PLAN_FORMAT_VERSION,
            "bindings": {
                "ruleset": {
                    "id": primary.package_id,
                    "version": primary.version,
                    "content_sha256": primary.sha256,
                },
                "campaign_setting": None,
                "package_closure_sha256": lock.closure_sha256,
            },
            "initial_state": {
                "mechanics": [{"namespace": package_id, "key": "state", "value": state}]
            },
        },
    )
    return cid


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    root = tmp_path_factory.mktemp("gm-reference")
    with AuthorityOwner.open(root / "campaign.db", owner_id="references") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="lite"
        )
        state = {
            "advancement": {
                "method": "fixed",
                "starting_level": 1,
                "maximum_level": 1,
                "recipients": "whole_party",
                "timing": "Remain at level one.",
                "allocation": "No level awards in this reference fixture.",
            }
        }
        cid = _start_reference_campaign(owner, candidate, state, "sylvia.srd-lite.bg3")
        yield owner, cid


def test_actual_classes_source_is_searchable_and_read_in_bounded_excerpts(campaign):
    owner, cid = campaign
    before = owner.snapshot(cid)
    result = owner.search_campaign_knowledge(
        cid, query="Monk Focus Points", sources=("ruleset",), limit=8
    )
    matches = [m for m in result["matches"] if m["path"].startswith("content/corpus/classes.json")]
    assert matches, "The valid 192 KB Classes file must not disappear at a reply-size bound"
    references = [
        owner.read_campaign_reference(cid, source="ruleset", path=m["path"]) for m in matches
    ]
    assert any("Monk" in r["content"] and "Focus" in r["content"] for r in references)
    assert all(len(r["content"].encode()) <= 65536 for r in references)
    assert all("body" not in m and "content" not in m for m in result["matches"])
    assert owner.snapshot(cid) == before


def test_direct_large_reference_returns_explicit_continuation(campaign):
    owner, cid = campaign
    first = owner.read_campaign_reference(cid, source="ruleset", path="content/corpus/classes.json")
    assert first["truncated"] is True and len(first["content"].encode()) <= 65536
    following = owner.read_campaign_reference(cid, source="ruleset", path=first["next_path"])
    assert following["selection"]["offset"] > first["selection"]["offset"]
    assert first["package"] == following["package"]


def test_exact_classes_reference_can_be_reconstructed_without_state_changes(campaign):
    owner, cid = campaign
    before = owner.snapshot(cid)
    reference = next(p for p in before.package_lock.entries if "reference" in p.package_id)
    parts = []
    path = "content/corpus/classes.json"
    while path is not None:
        page = owner.read_campaign_reference(cid, source="ruleset", path=path)
        assert page["package"] == {
            "id": reference.package_id,
            "version": reference.version,
            "content_sha256": reference.sha256,
        }
        assert len(canonical_json_bytes(page)) <= 65536
        parts.append(page["content"])
        path = page["next_path"]
    expected = owner._store.package_file_bytes(
        reference.package_id,
        reference.version,
        reference.sha256,
        "content/corpus/classes.json",
        maximum_bytes=64 * 1024 * 1024,
    )
    assert "".join(parts).encode() == expected
    assert owner.snapshot(cid) == before


def test_admitted_index_over_2048_files_is_searchable_with_explicit_unavailable_sources(tmp_path):
    source = Path(__file__).parent / "fixtures/rulesets/signal-and-static"
    record = json.loads((source / "package-record.json").read_text())
    payloads = {
        p.relative_to(source).as_posix(): p.read_bytes()
        for p in source.rglob("*")
        if p.is_file() and p.name != "package-record.json"
    }
    payloads.update({f"lore/entry-{i:04}.txt": b"Reference" for i in range(2050)})
    payloads["lore/entry-2049.txt"] = b"Lateindexneedle"
    payloads["lore/binary.txt"] = b"\xff"
    declaration_path = record["content_declarations"]
    declarations = json.loads(payloads[declaration_path])
    paths = sorted(path for path in payloads if path.startswith("lore/"))
    for offset in range(0, len(paths), 256):
        inclusion = deepcopy(declarations["inclusions"][0])
        inclusion["id"] = f"index-reference-{offset}"
        inclusion["links"] = {key: [] for key in inclusion["links"]}
        inclusion["links"]["artifacts"] = paths[offset : offset + 256]
        declarations["inclusions"].append(inclusion)
    declarations["inclusions"].sort(key=lambda item: item["id"])
    payloads[declaration_path] = canonical_json_bytes(declarations)
    with (
        materialize_package_archive((NormalizedPackageDraft(record, payloads),)) as archive,
        AuthorityOwner.open(tmp_path / "crowded.db", "crowded-reference") as owner,
    ):
        candidate = owner.install_package_archive(archive.archive, install_id="crowded")
        cid = _start_reference_campaign(owner, candidate, {}, record["id"])
        before = owner.snapshot(cid)
        found = owner.search_campaign_knowledge(cid, query="Lateindexneedle", sources=("ruleset",))
        assert found["matches"][0]["path"] == "lore/entry-2049.txt"
        assert found["unavailable_count"] == 1
        assert found["unavailable_references"] == [
            {"source": "ruleset", "path": "lore/binary.txt", "code": "campaign_reference_not_utf8"}
        ]
        assert (
            owner.read_campaign_reference(cid, source="ruleset", path="lore/entry-2049.txt")[
                "content"
            ]
            == "Lateindexneedle"
        )
        assert owner.snapshot(cid) == before


@pytest.mark.parametrize(
    "path",
    [
        "../content/corpus/classes.json",
        "content/corpus/classes.json#text/-1",
        "content/corpus/classes.json#text/9999999999",
        "content/corpus/classes.json#text/no",
    ],
)
def test_reference_excerpt_paths_reject_escape_and_invalid_offsets(campaign, path):
    owner, cid = campaign
    before = owner.snapshot(cid)
    with pytest.raises(CommandRejected):
        owner.read_campaign_reference(cid, source="ruleset", path=path)
    assert owner.snapshot(cid) == before
