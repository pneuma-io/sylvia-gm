"""Candidate knowledge must compose with real admitted and retained preparation."""

import hashlib
import io
import json
import zipfile
from copy import deepcopy

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    campaign_setting_entries,
    canonical_json_bytes,
    content_root_for_record,
)
from tests.test_alpha_f3_package_publication import _archive
from tests.test_campaign_rules_update import adopt
from tests.test_campaign_sequels import _request, _review
from tests.test_campaign_sequels import sequel as sequel


def knowledge_variant(owner, campaign_id, identity):
    """Keep each exact format/contract, changing only versions and additive knowledge."""
    lock = owner.current_package_lock(campaign_id).package_lock
    setting_ids = {entry.package_id for entry in campaign_setting_entries(lock)}
    candidate = next(item for item in owner.package_candidates() if item.package_lock == lock)
    exported = io.BytesIO()
    owner.export_package_candidate(candidate.candidate_id, exported)
    pending = {}
    with zipfile.ZipFile(exported) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for record in manifest["packages"]:
            package_id = record["id"]
            pending[package_id] = (
                deepcopy(record),
                {
                    file["path"]: archive.read(f"packages/{package_id}/{file['path']}")
                    for file in record["files"]
                },
            )
    completed = {}
    while pending:
        package_id = next(
            key
            for key, (record, _) in pending.items()
            if all(edge["id"] not in pending for edge in record["dependencies"])
        )
        record, files = pending.pop(package_id)
        record["version"] = "knowledge-update"
        for edge in record["dependencies"]:
            child = completed[edge["id"]][0]
            edge.update(version=child["version"], content_sha256=child["content_sha256"])
        if package_id in setting_ids:
            facts = (
                json.loads(files[CAMPAIGN_SETTING_KNOWLEDGE_PATH])
                if CAMPAIGN_SETTING_KNOWLEDGE_PATH in files
                else {
                    "format_id": "sylvia-gm-campaign-setting-knowledge",
                    "format_version": 1,
                    "facts": [],
                    "private_references": [],
                }
            )
            facts["facts"].append(
                {
                    "id": identity,
                    "fact": "A synthetic additive fact.",
                    "known_by": [],
                    "source_path": "locations/relay.md",
                }
            )
            facts["facts"].sort(key=lambda item: item["id"])
            files[CAMPAIGN_SETTING_KNOWLEDGE_PATH] = canonical_json_bytes(facts)
            path = record.get("content_declarations")
            if path:
                declarations = json.loads(files[path])
                links = declarations["inclusions"][0]["links"]["artifacts"]
                if CAMPAIGN_SETTING_KNOWLEDGE_PATH not in links:
                    links.append(CAMPAIGN_SETTING_KNOWLEDGE_PATH)
                    links.sort()
                files[path] = canonical_json_bytes(declarations)
        record["files"] = [
            {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for path, raw in sorted(files.items())
        ]
        record["content_sha256"] = content_root_for_record(record)
        completed[package_id] = record, files
    manifest["packages"] = [record for record, _ in completed.values()]
    raw = _archive(completed.values(), manifest_bytes=canonical_json_bytes(manifest))
    return owner.install_package_archive(io.BytesIO(raw), install_id="knowledge-variant")


@pytest.mark.parametrize("retained", [False, True])
def test_candidate_fact_collision_with_actual_plan_or_retained_adventure_is_blocked(
    sequel, retained
):
    owner, campaign, session, _database, _source = sequel
    if retained:
        owner.append_campaign_sequel(**_request(_review(owner, campaign, session)))
    prior = owner.campaign_knowledge_catalog(session)
    plan_fact = next(item for item in prior["secrets"] if item["source"] == "campaign_plan")
    changed = knowledge_variant(owner, campaign, plan_fact["id"])
    turn = owner.current_open_turn(campaign)
    if turn is not None:
        owner.complete_turn(campaign, turn)
    before = owner.snapshot(campaign)
    accepted = owner.accepted_campaign_plan(campaign)
    preview = owner.preview_campaign_rules_update(campaign, changed.candidate_id)
    assert not preview["compatible"] and preview["conflicts"]
    assert "knowledge" in str(preview["conflicts"]).lower()
    with pytest.raises(CommandRejected, match="conflicts"):
        adopt(owner, preview)
    assert owner.snapshot(campaign) == before
    assert owner.accepted_campaign_plan(campaign) == accepted
    assert owner.campaign_knowledge_catalog(session) == prior


def test_safe_additive_knowledge_composes_with_retained_adventures_and_reopens(sequel):
    owner, campaign, session, database, _source = sequel
    owner.append_campaign_sequel(**_request(_review(owner, campaign, session)))
    prior = owner.campaign_knowledge_catalog(session)
    changed = knowledge_variant(owner, campaign, "safe-additive-fact")
    turn = owner.current_open_turn(campaign)
    if turn is not None:
        owner.complete_turn(campaign, turn)
    accepted = owner.accepted_campaign_plan(campaign)
    before = owner.snapshot(campaign)
    preview = owner.preview_campaign_rules_update(campaign, changed.candidate_id)
    assert preview["compatible"], preview["conflicts"]
    adopt(owner, preview)
    after = owner.campaign_knowledge_catalog(session)
    assert {item["id"] for item in prior["secrets"]} < {item["id"] for item in after["secrets"]}
    assert owner.snapshot(campaign).state == before.state
    assert owner.accepted_campaign_plan(campaign) == accepted
    owner.close()
    with AuthorityOwner.open(database, "knowledge-reopen") as reopened:
        assert reopened.campaign_knowledge_catalog(session) == after
        assert reopened.accepted_campaign_plan(campaign) == accepted
