from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
from pathlib import Path
from typing import cast

import pytest

from sylvia_gm.agent._builder_session import (
    CampaignContentToolAuthority,
    CampaignPlanToolAuthority,
    _builder_skill_loader_tool,
    _campaign_content_tools,
    _campaign_plan_tools,
)
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_campaign_plans import bundled_plan
from sylvia_gm.application.bundled_packages import (
    _package_payload,
    _rift_drafts,
    bundled_campaign_content_catalog,
    bundled_rift_archive,
    bundled_rift_authority,
    bundled_srd_archive,
    read_bundled_rift_source,
)
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.builder._campaign_plan_materializer import materialize_campaign_plan_draft
from sylvia_gm.domain.authority import PackageRole, WriterRole
from sylvia_gm.domain.campaign_plan import (
    PackageBinding,
    parse_starting_party_policy,
    validate_campaign_plan,
)
from sylvia_gm.domain.package_library import (
    CAMPAIGN_SETTING_KNOWLEDGE_PATH,
    canonical_json_bytes,
    parse_campaign_setting_knowledge_bytes,
    validate_campaign_setting_knowledge_links,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from sylvia_gm.interfaces.owner_process import (
    _campaign_creation_catalog,
    _campaign_plan_tool_authority,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_ROOT = ROOT / "campaign-plans" / "the-rift-awakening"


def test_plan_skill_explains_required_player_character_before_promising_readiness():
    loaded = _builder_skill_loader_tool().handler({"name": "BUILD CAMPAIGN PLAN"}, None)
    assert loaded["success"] is True
    instructions = loaded["instructions"]
    assert "Every played campaign requires a Player Character" in instructions
    assert "character-free" in instructions
    assert "select or create" in instructions
    assert "saved Plan is not a ready-to-play campaign" in instructions


def test_bundled_package_text_identity_is_independent_of_checkout_line_endings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    (root / "content").mkdir(parents=True)
    (root / "content" / "entry.md").write_bytes(b"first\r\nsecond\r\n")
    (root / "runtime").mkdir()
    (root / "runtime" / "contract.yaml").write_bytes(b"value: one\r\n")
    (root / "content" / "opaque.bin").write_bytes(b"first\r\nsecond\r\n")

    payload = _package_payload(root)

    assert payload["content/entry.md"] == b"first\nsecond\n"
    assert payload["runtime/contract.yaml"] == b"value: one\n"
    assert payload["content/opaque.bin"] == b"first\r\nsecond\r\n"


SIGNAL_RULESET_ROOT = ROOT / "tests" / "fixtures" / "rulesets" / "signal-and-static"
SIGNAL_SETTING_ROOT = ROOT / "tests" / "fixtures" / "campaign-settings" / "signal-and-static"


def _payload() -> dict[str, object]:
    return json.loads((PLAN_ROOT / "plan.json").read_text(encoding="utf-8"))


def _tool_authority() -> CampaignPlanToolAuthority:
    authority = bundled_rift_authority()
    return CampaignPlanToolAuthority(
        ruleset=authority.ruleset,
        setting=authority.setting,
        closure_sha256=authority.closure_sha256,
        setting_paths=authority.setting_paths,
        read_setting=lambda path: read_bundled_rift_source(f"setting/{path}"),
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )


def _signal_archive(
    *,
    include_setting: bool = True,
    ruleset_version: str | None = None,
    setting_label: str | None = None,
) -> bytes:
    setting_record = json.loads(
        (SIGNAL_SETTING_ROOT / "package-record.json").read_text(encoding="utf-8")
    )
    if setting_label is not None:
        setting_record["metadata"]["label"] = setting_label
    dependency = {
        "namespace": "campaign_setting",
        "kind": "campaign_setting",
        "id": str(setting_record["id"]),
        "version": str(setting_record["version"]),
    }
    ruleset_record = json.loads(
        (SIGNAL_RULESET_ROOT / "package-record.json").read_text(encoding="utf-8")
    )
    if ruleset_version is not None:
        ruleset_record["version"] = ruleset_version
    ruleset_record["dependencies"] = [dependency] if include_setting else []
    drafts = [NormalizedPackageDraft(ruleset_record, _package_payload(SIGNAL_RULESET_ROOT))]
    if include_setting:
        drafts.append(NormalizedPackageDraft(setting_record, _package_payload(SIGNAL_SETTING_ROOT)))
    with materialize_package_archive(drafts) as materialized:
        return materialized.archive.read()


def _stale_same_version_rift_archive() -> bytes:
    """Materialize old Setting bytes behind the current bundled package versions."""

    ruleset, reference, setting = _rift_drafts()
    stale_payloads = dict(setting.payloads)
    changed_path = next(path for path in sorted(stale_payloads) if path.endswith(".md"))
    stale_payloads[changed_path] = stale_payloads[changed_path] + b"\nStale installed copy.\n"
    stale_setting = NormalizedPackageDraft(setting.record, stale_payloads, setting.format_version)
    with materialize_package_archive((ruleset, reference, stale_setting)) as materialized:
        return materialized.archive.read()


def test_genuine_rift_plan_binds_ruleset_and_severed_lands() -> None:
    authority = bundled_rift_authority()
    assert authority.ruleset.version == "0.2.0"
    assert authority.setting.version == "1.0.0-alpha.2"
    admitted = admit_package_archive(io.BytesIO(bundled_rift_archive()))
    assert [entry.package_id for entry in admitted.package_lock.entries] == [
        "sylvia.srd-lite.bg3",
        "pneuma.severed-lands",
        "pneuma.srd.5.2.1.reference",
    ]
    assert [
        dependency.package_id for dependency in admitted.package_lock.entries[0].dependencies
    ] == ["pneuma.srd.5.2.1.reference"]
    assert admitted.format_version == 4
    assert admitted.package_lock.entries[1].dependencies[0].kind == "ruleset_snapshot"

    validated = validate_campaign_plan(
        _payload(),
        readable_markdown=(PLAN_ROOT / "plan.md").read_text(encoding="utf-8"),
        expected_ruleset=authority.ruleset,
        expected_setting=authority.setting,
        expected_closure_sha256=authority.closure_sha256,
        setting_paths=authority.setting_paths,
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )
    assert validated.title == "The Rift Awakening"
    assert "campaigns/the-rift-awakening/act-6.md" in authority.setting_paths
    assert "locations/verdant-margin.md" in validated.referenced_setting_paths
    assert "npcs/v.md" in validated.referenced_setting_paths
    assert "Veycross" not in validated.readable_markdown
    assert "There is no going home" in validated.readable_markdown
    assert _payload()["opening_direction"].startswith(
        "Use a slow, welcoming, character-orienting D&D introduction"
    )
    assert "Directly tell the player who the selected character is" in _payload()[
        "opening_direction"
    ]
    assert "Let the player's first substantive choice decide what becomes important" in _payload()[
        "opening_direction"
    ]
    assert "A broad first observation only orients them to this same immediate situation" in (
        _payload()["opening_direction"]
    )
    assert "does not select or trigger a campaign lead" in _payload()["opening_direction"]
    assert "keep that past unestablished on later turns" in _payload()["opening_direction"]
    assert parse_starting_party_policy(_payload()["starting_party"]).as_payload() == {
        "player_character": {"mode": "compatible"},
        "required_companions": [],
        "custom_companions": {"minimum": 0, "maximum": 5},
    }
    facts = {item["id"]: item["fact"] for item in _payload()["initial_state"]["facts"]}
    assert facts["opening-presence"] == (
        "At the first narrated moment of play, the party is already present inside an Anchor "
        "Clearing in the Verdant Margin."
    )
    assert facts["opening-immediate-situation"] == (
        "At the first narrated moment, the selected party stands alone in an otherwise bare "
        "Anchor Clearing. Weathered stone fragments define its stable boundary. Dense Verdant "
        "Margin growth surrounds it with no obvious route or active incident, and the boundary "
        "is holding: the vegetation does not cross onto the bare ground."
    )
    assert facts["opening-history-unestablished"] == (
        "The party's presence in the Verdant Margin is the Campaign File's earliest moment. "
        "At that opening moment, the player character had not yet made a first choice, and no "
        "earlier player action or shared party history was established."
    )
    knowledge = parse_campaign_setting_knowledge_bytes(
        (
            ROOT / "campaign-settings" / "severed-lands" / CAMPAIGN_SETTING_KNOWLEDGE_PATH
        ).read_bytes()
    )
    assert authority.setting_knowledge_ids == frozenset(item.identity for item in knowledge.facts)
    assert knowledge.private_references == ("DM-SECRETS.md",)
    assert all("fact" not in secret for secret in _payload()["secrets"])
    setting_manifest = next(
        item for item in admitted.manifests if item.package_id == authority.setting.package_id
    )
    validate_campaign_setting_knowledge_links(setting_manifest, knowledge)


def test_bundled_catalog_loads_rift_with_current_package_bindings(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "bundled-catalog.sqlite3", "owner-catalog") as owner:
        catalog = _campaign_creation_catalog(tmp_path, owner)
        assert any(item["kind"] == "ruleset" and item["id"] == "sylvia.srd-lite.bg3"
                   for item in catalog)
        assert any(item["kind"] == "campaign_plan" and item["title"] == "Rift Awakening"
                   for item in catalog)


def test_creation_catalog_groups_raw_candidates_into_compatible_logical_choices(
    tmp_path: Path,
) -> None:
    with AuthorityOwner.open(tmp_path / "creation-catalog.sqlite3", "owner-catalog") as owner:
        clean_catalog = _campaign_creation_catalog(tmp_path, owner)
        clean_srd = [
            item
            for item in clean_catalog
            if item["kind"] == "ruleset" and item["id"] == "sylvia.srd-lite.bg3"
        ]
        assert len(clean_srd) == 1
        assert clean_srd[0]["selection_id"] == "sylvia.srd-lite.bg3"
        assert clean_srd[0]["compatible_settings"][0]["id"] == "pneuma.severed-lands"
        plan = next(item for item in clean_catalog
                    if item["kind"] == "campaign_plan" and item["id"] == "rift-awakening")
        assert plan["starting_party"] == {
            "player_character": {"mode": "compatible"},
            "required_companions": [],
            "custom_companions": {"minimum": 1, "maximum": 5},
        }

        owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="catalog-bare-srd-a"
        )
        owner.install_package_archive(
            io.BytesIO(bundled_srd_archive()), install_id="catalog-bare-srd-b"
        )
        owner.install_package_archive(
            io.BytesIO(bundled_rift_archive()), install_id="catalog-rift-closure"
        )
        signal = owner.install_package_archive(
            io.BytesIO(_signal_archive()), install_id="catalog-signal-closure"
        )

        catalog = _campaign_creation_catalog(tmp_path, owner)

    rulesets = [item for item in catalog if item["kind"] == "ruleset"]
    srd = [item for item in rulesets if item["id"] == "sylvia.srd-lite.bg3"]
    assert len(srd) == 1
    # Materializing an already offered bundled choice must not invalidate an Interview selection.
    assert srd[0]["selection_id"] == "sylvia.srd-lite.bg3"
    assert srd[0]["title"] == "D&D 5e SRD 5.2.1"
    assert srd[0]["compatible_settings"] == (
        {
            "id": "pneuma.severed-lands",
            "version": "1.0.0-alpha.2",
            "content_sha256": bundled_rift_authority().setting.content_sha256,
            "candidate_id": "candidate-catalog-rift-closure",
            "ruleset": bundled_rift_authority().ruleset.as_payload(),
        },
    )
    assert all(item["selection_id"] != "the-rift-awakening" for item in rulesets)

    signal_primary = next(
        entry for entry in signal.package_lock.entries if entry.role is PackageRole.PRIMARY
    )
    signal_choice = next(item for item in rulesets if item["id"] == signal_primary.package_id)
    assert signal_choice["selection_id"] == signal.candidate_id
    assert len(signal_choice["compatible_settings"]) == 1

    rift_plan = next(
        item
        for item in catalog
        if item["kind"] == "campaign_plan" and item["id"] == "rift-awakening"
    )
    assert rift_plan["ruleset"]["id"] == srd[0]["id"]
    assert rift_plan["campaign_setting"]["id"] == "pneuma.severed-lands"


def test_creation_catalog_enumerates_candidates_once_and_refreshes_next_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with AuthorityOwner.open(tmp_path / "catalog-snapshot.sqlite3", "catalog-snapshot") as owner:
        calls = 0
        original_package_candidates = owner.package_candidates

        def counted_package_candidates():
            nonlocal calls
            calls += 1
            return original_package_candidates()

        monkeypatch.setattr(owner, "package_candidates", counted_package_candidates)

        first = _campaign_creation_catalog(tmp_path, owner)
        assert calls == 1
        assert not any(item.get("id") == "org.example.signal-and-static" for item in first)

        imported = owner.install_package_archive(
            io.BytesIO(_signal_archive()), install_id="catalog-snapshot-new-import",
        )
        second = _campaign_creation_catalog(tmp_path, owner)

        assert calls == 2
        primary = next(
            entry for entry in imported.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        assert any(
            item.get("kind") == "ruleset"
            and item.get("id") == primary.package_id
            and item.get("content_sha256") == primary.sha256
            for item in second
        )


def test_creation_catalog_keeps_current_bundle_over_stale_equal_version_closure(
    tmp_path: Path,
) -> None:
    authority = bundled_rift_authority()
    with AuthorityOwner.open(tmp_path / "stale-catalog.sqlite3", "owner-stale-catalog") as owner:
        stale = owner.install_package_archive(
            io.BytesIO(_stale_same_version_rift_archive()),
            install_id="older-equal-version-rift",
        )
        existing_campaign = owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-locked-to-stale-rift",
                display_name="Existing stale-lock campaign",
                writer=WriterRole.DM,
                package_lock=stale.package_lock,
                turn_id="turn-locked-to-stale-rift",
            )
        )
        lock_before_catalog = owner.current_package_lock(existing_campaign.campaign_id)

        catalog = _campaign_creation_catalog(tmp_path, owner)

        assert any(
            candidate.candidate_id == stale.candidate_id for candidate in owner.package_candidates()
        )
        assert owner.current_package_lock(existing_campaign.campaign_id) == lock_before_catalog

        selected_ruleset = next(
            item
            for item in catalog
            if item["kind"] == "ruleset" and item["id"] == authority.ruleset.package_id
        )
        draft = owner.create_guided_campaign_draft(
            operation_id="create-current-bundle-draft",
            display_name="Current bundle draft",
            builder_session_id="session-current-bundle-draft",
            builder_provider_id="test.provider",
            builder_model_id="test.model",
        )
        revision = 0
        for ordinal, (step, visible_answer, answer) in enumerate(
            (
                (
                    "ruleset",
                    "D&D 5e SRD 5.2.1",
                    {"candidate_id": selected_ruleset["selection_id"]},
                ),
                (
                    "setting",
                    "Severed Lands",
                    {"setting_id": authority.setting.package_id},
                ),
                (
                    "plan",
                    "Rift Awakening",
                    {"plan_id": "rift-awakening"},
                ),
                (
                    "name",
                    "Current bundle draft",
                    {"display_name": "Current bundle draft"},
                ),
            )
        ):
            draft = owner.advance_guided_campaign_draft(
                operation_id=f"current-bundle-answer-{ordinal}",
                campaign_id=draft.campaign_id,
                builder_session_id=draft.builder_session_id,
                expected_construction_revision=revision,
                current_step=step,
                visible_answer=visible_answer,
                answer=answer,
            )
            revision = draft.construction_revision
        assert draft.current_step == "participants"

    ruleset = next(
        item
        for item in catalog
        if item["kind"] == "ruleset" and item["id"] == authority.ruleset.package_id
    )
    setting = next(
        item
        for item in catalog
        if item["kind"] == "campaign_setting"
        and item["id"] == authority.setting.package_id
        and item["content_sha256"] == authority.setting.content_sha256
    )
    plan = next(
        item
        for item in catalog
        if item["kind"] == "campaign_plan" and item["id"] == "rift-awakening"
    )

    assert ruleset["selection_id"] == authority.ruleset.package_id
    assert ruleset["content_sha256"] == authority.ruleset.content_sha256
    assert ruleset["compatible_settings"] == (
        {
            "id": authority.setting.package_id,
            "version": authority.setting.version,
            "content_sha256": authority.setting.content_sha256,
            "candidate_id": None,
            "ruleset": {
                "id": authority.ruleset.package_id,
                "version": authority.ruleset.version,
                "content_sha256": authority.ruleset.content_sha256,
            },
        },
    )
    assert setting["title"] == "Severed Lands"
    assert plan["ruleset"] == {
        "id": authority.ruleset.package_id,
        "version": authority.ruleset.version,
        "content_sha256": authority.ruleset.content_sha256,
    }
    assert plan["campaign_setting"] == (
        bundled_plan("rift-awakening").plan.payload["bindings"]["campaign_setting"]
    )


def test_creation_catalog_prefers_latest_successful_same_version_and_higher_versions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "creation-replacement.sqlite3"
    with AuthorityOwner.open(database, "owner-replacement") as owner:
        owner.install_package_archive(
            io.BytesIO(_signal_archive(include_setting=False)),
            install_id="zzzz-older-bare",
        )
        owner.install_package_archive(
            io.BytesIO(_signal_archive(setting_label="Older setting")),
            install_id="zzz-older-closure",
        )
        latest = owner.install_package_archive(
            io.BytesIO(_signal_archive(setting_label="Latest setting")),
            install_id="aaa-latest-closure",
        )
        catalog = _campaign_creation_catalog(tmp_path, owner)
        choice = next(
            item
            for item in catalog
            if item["kind"] == "ruleset" and item["id"] == "sylvia-gm.conformance.signal-static"
        )
        assert choice["selection_id"] == latest.candidate_id

    with AuthorityOwner.open(database, "owner-replacement-reopened") as reopened:
        choice = next(
            item
            for item in _campaign_creation_catalog(tmp_path, reopened)
            if item["kind"] == "ruleset" and item["id"] == "sylvia-gm.conformance.signal-static"
        )
        assert choice["selection_id"] == latest.candidate_id

    version_database = tmp_path / "creation-version.sqlite3"
    with AuthorityOwner.open(version_database, "owner-version") as owner:
        higher = owner.install_package_archive(
            io.BytesIO(_signal_archive(ruleset_version="2.0.0")),
            install_id="older-higher-version",
        )
        owner.install_package_archive(
            io.BytesIO(_signal_archive(ruleset_version="1.0.0")),
            install_id="newer-lower-version",
        )
        choice = next(
            item
            for item in _campaign_creation_catalog(tmp_path, owner)
            if item["kind"] == "ruleset" and item["id"] == "sylvia-gm.conformance.signal-static"
        )
        assert choice["selection_id"] == higher.candidate_id


def test_builder_discovers_bundled_campaign_hierarchy_and_prepares_setup() -> None:
    catalog = tuple(cast(dict[str, object], item) for item in bundled_campaign_content_catalog())
    tools = {
        tool.name: tool
        for tool in _campaign_content_tools(
            CampaignContentToolAuthority(items=cast(tuple[dict[str, object], ...], catalog))
        )
    }

    discovered = tools["discover_campaign_content"].handler({"query": "Rift Awakening"}, None)
    prepared = tools["prepare_campaign_setup"].handler(
        {"campaign_plan_id": "rift-awakening"}, None
    )

    assert discovered["status"] == "available"
    assert [item["kind"] for item in discovered["items"]] == ["campaign_plan"]
    plan = discovered["items"][0]
    assert plan["title"] == "Rift Awakening"
    assert plan["ruleset"]["id"] == "sylvia.srd-lite.bg3"
    assert plan["campaign_setting"]["id"] == "pneuma.severed-lands"
    assert prepared == {
        "success": True,
        "status": "setup_ready",
        "campaign_plan_id": "rift-awakening",
        "title": "Rift Awakening",
        "player_character_required": True,
        "message": (
            "Use New Campaign to confirm the required Player Character and other player "
            "choices. Gameplay has not started."
        ),
    }


@pytest.mark.parametrize("query", ["D&D SRD 5.2.1", "5.2.1 srd D&D", "D&D   SRD  5.2.1"])
def test_builder_finds_ruleset_without_requiring_its_exact_display_title(query: str) -> None:
    catalog = tuple(cast(dict[str, object], item) for item in bundled_campaign_content_catalog())
    tools = {tool.name: tool for tool in _campaign_content_tools(
        CampaignContentToolAuthority(items=catalog)
    )}
    discover = tools["discover_campaign_content"].handler
    found = discover({"query": query}, None)
    assert any(item["kind"] == "ruleset" and item["id"] == "sylvia.srd-lite.bg3"
               for item in found["items"])
    assert discover({"query": "D&D SRD 42.1.3"}, None)["status"] == "no_matches"


def test_builder_campaign_content_tools_refresh_after_same_turn_publication() -> None:
    published = {"items": ({
        "kind": "campaign_plan",
        "id": "campaign-plan-reusable-existing",
        "title": "Existing Plan",
    },)}
    authority = CampaignContentToolAuthority(
        items=published["items"],
        refresh=lambda: cast(tuple[dict[str, object], ...], published["items"]),
    )
    tools = {tool.name: tool for tool in _campaign_content_tools(authority)}

    assert tools["discover_campaign_content"].handler(
        {"query": "Newly Published"}, None
    )["status"] == "no_matches"

    published["items"] = (*published["items"], {
        "kind": "campaign_plan",
        "id": "campaign-plan-reusable-new",
        "title": "Newly Published",
    })

    discovered = tools["discover_campaign_content"].handler(
        {"query": "Newly Published"}, None
    )
    prepared = tools["prepare_campaign_setup"].handler(
        {"campaign_plan_id": "campaign-plan-reusable-new"}, None
    )
    assert [item["id"] for item in discovered["items"]] == [
        "campaign-plan-reusable-new"
    ]
    assert prepared["status"] == "setup_ready"
    assert prepared["title"] == "Newly Published"


def test_builder_prepares_generic_campaign_plan_without_adapter_setup_id() -> None:
    plan = {
        "kind": "campaign_plan",
        "id": "campaign-plan-reusable-generic",
        "title": "Signal Relay",
    }
    tools = {
        tool.name: tool
        for tool in _campaign_content_tools(CampaignContentToolAuthority(items=(plan,)))
    }

    prepared = tools["prepare_campaign_setup"].handler(
        {"campaign_plan_id": "campaign-plan-reusable-generic"}, None
    )

    assert prepared == {
        "success": True,
        "status": "setup_ready",
        "campaign_plan_id": "campaign-plan-reusable-generic",
        "title": "Signal Relay",
        "player_character_required": True,
        "message": (
            "Use New Campaign to confirm the required Player Character and other player "
            "choices. Gameplay has not started."
        ),
    }


def test_campaign_setting_knowledge_rejects_ambiguous_or_missing_sources() -> None:
    authority = bundled_rift_authority()
    admitted = admit_package_archive(io.BytesIO(bundled_rift_archive()))
    setting_manifest = next(
        item for item in admitted.manifests if item.package_id == authority.setting.package_id
    )
    unsorted = {
        "format_id": "sylvia-gm-campaign-setting-knowledge",
        "format_version": 1,
        "facts": [
            {
                "id": "z-secret",
                "fact": "First private fact.",
                "known_by": [],
                "source_path": "DM-SECRETS.md",
            },
            {
                "id": "a-secret",
                "fact": "Second private fact.",
                "known_by": [],
                "source_path": "DM-SECRETS.md",
            },
        ],
        "private_references": ["DM-SECRETS.md"],
    }
    with pytest.raises(ValueError, match="sorted and unique"):
        parse_campaign_setting_knowledge_bytes(canonical_json_bytes(unsorted))

    missing_source = copy.deepcopy(unsorted)
    missing_source["facts"] = [
        {
            "id": "missing-secret",
            "fact": "A private fact with no admitted source.",
            "known_by": [],
            "source_path": "missing-private-source.md",
        }
    ]
    knowledge = parse_campaign_setting_knowledge_bytes(canonical_json_bytes(missing_source))
    with pytest.raises(ValueError, match="missing package content"):
        validate_campaign_setting_knowledge_links(setting_manifest, knowledge)


def test_campaign_plan_rejects_wrong_binding_missing_reference_and_private_path() -> None:
    authority = bundled_rift_authority()
    base = _payload()

    wrong_binding = copy.deepcopy(base)
    wrong_binding["bindings"]["campaign_setting"]["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="bindings do not match"):
        validate_campaign_plan(
            wrong_binding,
            readable_markdown="Readable",
            expected_ruleset=authority.ruleset,
            expected_setting=authority.setting,
            expected_closure_sha256=authority.closure_sha256,
            setting_paths=authority.setting_paths,
            setting_knowledge_ids=authority.setting_knowledge_ids,
        )

    missing = copy.deepcopy(base)
    missing["starting_location"]["path"] = "locations/not-real.md"
    with pytest.raises(ValueError, match="missing Setting content"):
        validate_campaign_plan(
            missing,
            readable_markdown="Readable",
            expected_ruleset=authority.ruleset,
            expected_setting=authority.setting,
            expected_closure_sha256=authority.closure_sha256,
            setting_paths=authority.setting_paths,
            setting_knowledge_ids=authority.setting_knowledge_ids,
        )

    missing_knowledge = copy.deepcopy(base)
    missing_knowledge["secrets"][0]["id"] = "missing-setting-secret"
    missing_knowledge["secrets"][0]["setting_fact_id"] = "missing-setting-secret"
    with pytest.raises(ValueError, match="missing Campaign Setting knowledge"):
        validate_campaign_plan(
            missing_knowledge,
            readable_markdown="Readable",
            expected_ruleset=authority.ruleset,
            expected_setting=authority.setting,
            expected_closure_sha256=authority.closure_sha256,
            setting_paths=authority.setting_paths,
            setting_knowledge_ids=authority.setting_knowledge_ids,
        )

    private = copy.deepcopy(base)
    private["side_content"].append("C:\\Users\\someone\\private.txt")
    with pytest.raises(ValueError, match="absolute path"):
        validate_campaign_plan(
            private,
            readable_markdown="Readable",
            expected_ruleset=authority.ruleset,
            expected_setting=authority.setting,
            expected_closure_sha256=authority.closure_sha256,
            setting_paths=authority.setting_paths,
            setting_knowledge_ids=authority.setting_knowledge_ids,
        )


def test_campaign_plan_opening_direction_is_optional_private_creative_guidance() -> None:
    authority = bundled_rift_authority()
    base = copy.deepcopy(_payload())
    base.pop("opening_direction")
    without_direction = validate_campaign_plan(
        base,
        readable_markdown="Readable",
        expected_ruleset=authority.ruleset,
        expected_setting=authority.setting,
        expected_closure_sha256=authority.closure_sha256,
        setting_paths=authority.setting_paths,
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )
    with_direction_payload = copy.deepcopy(base)
    with_direction_payload["opening_direction"] = (
        "Open in media res with immediate danger, then establish identities as play permits."
    )
    with_direction = validate_campaign_plan(
        with_direction_payload,
        readable_markdown="Readable",
        expected_ruleset=authority.ruleset,
        expected_setting=authority.setting,
        expected_closure_sha256=authority.closure_sha256,
        setting_paths=authority.setting_paths,
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )

    assert "opening_direction" not in without_direction.payload
    assert with_direction.payload["opening_direction"].startswith("Open in media res")

    legacy_party = copy.deepcopy(base)
    legacy_party["starting_party"] = {
        "companions": [],
        "player_characters": [],
        "requirements": [],
    }
    with pytest.raises(ValueError, match="Starting party fields are invalid"):
        validate_campaign_plan(
            legacy_party,
            readable_markdown="Readable",
            expected_ruleset=authority.ruleset,
            expected_setting=authority.setting,
            expected_closure_sha256=authority.closure_sha256,
            setting_paths=authority.setting_paths,
            setting_knowledge_ids=authority.setting_knowledge_ids,
        )


def test_general_builder_can_inspect_and_publish_campaign_plan(tmp_path: Path) -> None:
    draft = tmp_path / "campaign-plan-draft"
    draft.mkdir()
    shutil.copy2(PLAN_ROOT / "plan.json", draft / "plan.json")
    shutil.copy2(PLAN_ROOT / "plan.md", draft / "plan.md")
    authority = bundled_rift_authority()

    materialized = materialize_campaign_plan_draft(
        tmp_path,
        expected_ruleset=authority.ruleset,
        expected_setting=authority.setting,
        expected_closure_sha256=authority.closure_sha256,
        setting_paths=authority.setting_paths,
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )
    tools = {tool.name: tool for tool in _campaign_plan_tools(tmp_path, _tool_authority())}
    listing = tools["inspect_campaign_sources"].handler(
        {"action": "list", "query": "waystation"}, None
    )
    source = tools["inspect_campaign_sources"].handler(
        {"action": "read", "path": "setting/locations/waystation.md"}, None
    )
    published = tools["publish_campaign_plan"].handler({}, None)

    assert listing["success"] is True
    assert listing["status"] == "available"
    assert "setting/locations/waystation.md" in listing["paths"]
    assert source["success"] is True
    assert "Anchor Door" in source["content"]
    assert published["status"] == "ready"
    assert published["player_character_required"] is True
    assert "Starting play still requires a Player Character" in published["message"]
    assert published["artifact_id"] == materialized.plan.artifact_id
    assert published["tracked_skill"]["name"] == "BUILD CAMPAIGN PLAN"
    assert len(published["tracked_skill"]["sha256"]) == 64
    assert published["plan_path"] == "campaign-plan.json"
    assert published["readable_path"] == "campaign-plan.md"
    assert "# The Rift Awakening" in published["readable_preview"]
    assert json.loads((tmp_path / "campaign-plan.json").read_text(encoding="utf-8")) == _payload()
    assert (tmp_path / "campaign-plan.md").read_text(encoding="utf-8") == (
        PLAN_ROOT / "plan.md"
    ).read_text(encoding="utf-8").strip()
    assert "absolute" not in json.dumps(published).casefold()


def test_campaign_plan_tools_follow_the_selected_package_authority(tmp_path: Path) -> None:
    authority = CampaignPlanToolAuthority(
        ruleset=PackageBinding("example.signal-rules", "2.0.0", "1" * 64),
        setting=PackageBinding("example.signal-setting", "3.0.0", "2" * 64),
        closure_sha256="3" * 64,
        setting_paths=frozenset({"locations/relay.md", "people/operator.md"}),
        read_setting=lambda path: {
            "locations/relay.md": "# Relay Station",
            "people/operator.md": "# Operator",
        }[path],
    )
    tools = {tool.name: tool for tool in _campaign_plan_tools(tmp_path, authority)}
    identity = tools["inspect_campaign_sources"].handler(
        {"action": "read", "path": "ruleset/identity.json"}, None
    )
    example = tools["inspect_campaign_sources"].handler(
        {"action": "read", "path": "campaign-plan/example/plan.json"}, None
    )
    source = tools["inspect_campaign_sources"].handler(
        {"action": "read", "path": "setting/locations/relay.md"}, None
    )

    assert "example.signal-rules" in identity["content"]
    assert "wotc.srd" not in identity["content"]
    assert source["content"] == "# Relay Station"

    draft = tmp_path / "campaign-plan-draft"
    draft.mkdir()
    draft.joinpath("plan.json").write_text(example["content"], encoding="utf-8")
    draft.joinpath("plan.md").write_text("# Signal intrusion", encoding="utf-8")
    published = tools["publish_campaign_plan"].handler({}, None)
    assert published["success"] is True
    assert published["ruleset"]["id"] == "example.signal-rules"
    assert published["campaign_setting"]["id"] == "example.signal-setting"


def test_agent_friendly_draft_tool_materializes_and_publishes_closed_plan(tmp_path: Path) -> None:
    authority = CampaignPlanToolAuthority(
        ruleset=PackageBinding("example.signal-rules", "2.0.0", "1" * 64),
        setting=PackageBinding("example.signal-setting", "3.0.0", "2" * 64),
        closure_sha256="3" * 64,
        setting_paths=frozenset({"characters/operator.md", "locations/relay.md"}),
        read_setting=lambda path: f"# {path}",
    )
    tools = {tool.name: tool for tool in _campaign_plan_tools(tmp_path, authority)}
    created = tools["create_campaign_plan_draft"].handler(
        {
            "title": "Relay Intrusion",
            "premise": "An operator must stop a hostile signal from taking the relay.",
            "tone": "Tense science fiction",
            "player_character_mode": "fixed",
            "player_character_path": "characters/operator.md",
            "required_companion_paths": [],
            "custom_companion_minimum": 0,
            "custom_companion_maximum": 0,
            "starting_location_path": "locations/relay.md",
            "opening_situation": "The relay begins broadcasting an impossible response.",
            "primary_objective": "Secure the relay before the signal completes its handshake.",
            "completion_conditions": [
                "The signal is disabled or the relay abandoned, "
                "and the keeper has responded to that outcome.",
            ],
        },
        None,
    )
    published = tools["publish_campaign_plan"].handler({}, None)

    assert created["status"] == "draft_ready"
    assert published["status"] == "ready"
    payload = json.loads((tmp_path / "campaign-plan.json").read_text(encoding="utf-8"))
    assert payload["format_id"] == "sylvia-gm-campaign-plan"
    assert payload["starting_party"]["player_character"]["character"]["path"] == (
        "characters/operator.md"
    )
    assert payload["starting_location"]["path"] == "locations/relay.md"


def test_agent_friendly_draft_accepts_paths_returned_by_source_inspection(tmp_path: Path) -> None:
    authority = CampaignPlanToolAuthority(
        ruleset=PackageBinding("example.signal-rules", "2.0.0", "1" * 64),
        setting=PackageBinding("example.signal-setting", "3.0.0", "2" * 64),
        closure_sha256="3" * 64,
        setting_paths=frozenset({"characters/operator.md", "locations/relay.md"}),
        read_setting=lambda path: f"# {path}",
    )
    tools = {tool.name: tool for tool in _campaign_plan_tools(tmp_path, authority)}

    created = tools["create_campaign_plan_draft"].handler(
        {
            "title": "Relay Intrusion",
            "premise": "An operator must stop a hostile signal from taking the relay.",
            "tone": "Tense science fiction",
            "player_character_mode": "compatible",
            "required_companion_paths": [],
            "custom_companion_minimum": 0,
            "custom_companion_maximum": 2,
            "starting_location_path": "setting/locations/relay.md",
            "opening_situation": "The relay begins broadcasting an impossible response.",
            "opening_direction": "Open in media res as the impossible response begins.",
            "primary_objective": "Secure the relay before the signal completes its handshake.",
            "completion_conditions": [
                "The signal is disabled or the relay abandoned, "
                "and the keeper has responded to that outcome.",
            ],
        },
        None,
    )

    assert created["status"] == "draft_ready"
    payload = json.loads(
        (tmp_path / "campaign-plan-draft" / "plan.json").read_text(encoding="utf-8")
    )
    assert payload["starting_party"] == {
        "player_character": {"mode": "compatible"},
        "required_companions": [],
        "custom_companions": {"minimum": 0, "maximum": 2},
    }
    assert payload["opening_direction"].startswith("Open in media res")
    assert payload["starting_location"]["path"] == "locations/relay.md"


def test_owner_projects_campaign_plan_tools_from_the_current_exact_lock(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-plan-tools") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(bundled_rift_archive()),
            install_id="install-rift-plan-tools",
        )
        campaign = owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-plan-tools",
                display_name="Plan tools",
                writer=WriterRole.BUILDER,
                package_lock=candidate.package_lock,
                turn_id="turn-plan-tools",
            )
        )
        authority = _campaign_plan_tool_authority(owner, campaign.campaign_id)

        assert authority.ruleset.package_id == "sylvia.srd-lite.bg3"
        assert authority.setting.package_id == "pneuma.severed-lands"
        assert "locations/waystation.md" in authority.setting_paths
        assert "Anchor Door" in authority.read_setting("locations/waystation.md")


def test_neutral_ruleset_and_setting_closure_exposes_its_own_plan_sources(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-signal-plan-tools") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_signal_archive()),
            install_id="install-signal-plan-tools",
        )
        campaign = owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-signal-plan-tools",
                display_name="Signal plan tools",
                writer=WriterRole.BUILDER,
                package_lock=candidate.package_lock,
                turn_id="turn-signal-plan-tools",
            )
        )
        authority = _campaign_plan_tool_authority(owner, campaign.campaign_id)

        assert authority.ruleset.package_id == "sylvia-gm.conformance.signal-static"
        assert authority.setting.package_id == "sylvia-gm.conformance.signal-setting"
        assert "locations/relay.md" in authority.setting_paths
        assert "Relay Station" in authority.read_setting("locations/relay.md")
        owner.create_agent_session(
            campaign_id=campaign.campaign_id,
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-signal-plan-tools",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.record_builder_evidence(
            session_id="session-signal-plan-tools",
            evidence_id="evidence-signal-plan-tools",
            source_kind="user_provided",
            content_sha256=hashlib.sha256(b"signal premise").hexdigest(),
            source_locator=None,
            safe_excerpt="An operator investigates an impossible relay signal.",
            claims=("relay",),
        )
        plan_tools = {tool.name: tool for tool in _campaign_plan_tools(tmp_path, authority)}
        plan_tools["create_campaign_plan_draft"].handler(
            {
                "title": "Relay Intrusion",
                "premise": "An operator must stop a hostile signal from taking the relay.",
                "tone": "Tense science fiction",
                "player_character_mode": "fixed",
                "player_character_path": "characters/operator.md",
                "required_companion_paths": [],
                "custom_companion_minimum": 0,
                "custom_companion_maximum": 0,
                "starting_location_path": "locations/relay.md",
                "opening_situation": "The relay broadcasts an impossible response.",
                "primary_objective": "Secure the relay.",
                "completion_conditions": [
                "The signal is disabled or the relay abandoned, "
                "and the keeper has responded to that outcome.",
            ],
            },
            None,
        )
        structured = json.loads(
            (tmp_path / "campaign-plan-draft" / "plan.json").read_text(encoding="utf-8")
        )
        readable = (tmp_path / "campaign-plan-draft" / "plan.md").read_text(encoding="utf-8")
        content = {
            "campaign_plan": structured,
            "campaign_plan_markdown": readable,
        }
        owner.admit_campaign_plan(
            plan_id="plan-signal-reference",
            campaign_id=campaign.campaign_id,
            builder_session_id="session-signal-plan-tools",
            expected_revision=0,
            expected_package_lock_id=owner.current_package_lock(campaign.campaign_id).lock_id,
            content=content,
            content_sha256=hashlib.sha256(canonical_json_bytes(content)).hexdigest(),
            provenance_evidence_ids=("evidence-signal-plan-tools",),
            validation_outcome="valid",
            acceptance_status="accepted",
        )
        mcp = StatelessMcpMechanics(owner)
        assert "read_campaign_reference" in {
            tool.name for tool in mcp.list_tools(campaign.campaign_id)
        }
        reference = mcp.read_reference(
            campaign.campaign_id,
            source="campaign_setting",
            path="locations/relay.md",
        )
        assert "Relay Station" in str(reference["content"])
        assert reference["package"]["id"] == "sylvia-gm.conformance.signal-setting"


def test_campaign_plan_materializer_rejects_duplicate_keys(tmp_path: Path) -> None:
    draft = tmp_path / "campaign-plan-draft"
    draft.mkdir()
    draft.joinpath("plan.json").write_text('{"format_id":"a","format_id":"b"}')
    draft.joinpath("plan.md").write_text("Readable", encoding="utf-8")
    authority = bundled_rift_authority()
    with pytest.raises(ValueError, match="duplicate key"):
        materialize_campaign_plan_draft(
            tmp_path,
            expected_ruleset=authority.ruleset,
            expected_setting=authority.setting,
            expected_closure_sha256=authority.closure_sha256,
            setting_paths=authority.setting_paths,
        )
