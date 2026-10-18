from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sylvia_gm.application.bundled_packages import (
    bundled_rift_authority,
    bundled_rift_campaign_plan,
)
from sylvia_gm.application.reusable_plans import (
    load_reusable_campaign_plan,
    reusable_campaign_plan_catalog,
    save_reusable_campaign_plan,
    save_reusable_rift_plan,
)
from sylvia_gm.domain.campaign_plan import (
    PackageBinding,
    ValidatedCampaignPlan,
    validate_campaign_plan,
)
from sylvia_gm.domain.package_library import canonical_json_bytes


def _signal_plan() -> tuple[dict[str, object], str]:
    payload = json.loads(canonical_json_bytes(bundled_rift_campaign_plan().payload))
    payload["id"] = "signal-echoes"
    payload["title"] = "Echoes Across Static"
    payload["premise"] = "Trace a missing signal through an unfamiliar system."
    payload["bindings"] = {
        "ruleset": {
            "id": "example.signal-rules",
            "version": "2.0.0",
            "content_sha256": "1" * 64,
        },
        "campaign_setting": {
            "id": "example.signal-setting",
            "version": "3.0.0",
            "content_sha256": "2" * 64,
        },
        "package_closure_sha256": "3" * 64,
    }
    payload["boundaries"] = ["Keep the synthetic signal boundary exactly as authored."]
    payload["starting_party"] = {
        "player_character": {"mode": "compatible"},
        "required_companions": [],
        "custom_companions": {"minimum": 0, "maximum": 0},
    }
    markdown = "# Echoes Across Static\n\nTrace a missing signal through an unfamiliar system."
    return payload, markdown


def _validate_signal_plan(payload: object, readable_markdown: str) -> ValidatedCampaignPlan:
    authority = bundled_rift_authority()
    return validate_campaign_plan(
        payload,
        readable_markdown=readable_markdown,
        expected_ruleset=PackageBinding("example.signal-rules", "2.0.0", "1" * 64),
        expected_setting=PackageBinding("example.signal-setting", "3.0.0", "2" * 64),
        expected_closure_sha256="3" * 64,
        setting_paths=authority.setting_paths,
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )


def _validate_wrong_ruleset(payload: object, readable_markdown: str) -> ValidatedCampaignPlan:
    authority = bundled_rift_authority()
    return validate_campaign_plan(
        payload,
        readable_markdown=readable_markdown,
        expected_ruleset=PackageBinding("example.other-rules", "2.0.0", "4" * 64),
        expected_setting=PackageBinding("example.signal-setting", "3.0.0", "2" * 64),
        expected_closure_sha256="3" * 64,
        setting_paths=authority.setting_paths,
        setting_knowledge_ids=authority.setting_knowledge_ids,
    )


def test_generic_plan_preserves_exact_validated_content_and_bindings(tmp_path: Path) -> None:
    payload, markdown = _signal_plan()

    def save():
        return save_reusable_campaign_plan(
            tmp_path,
            operation_id="publish-signal-plan",
            source_payload=payload,
            source_markdown=markdown,
            validate_plan=_validate_signal_plan,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        saved, replay = tuple(executor.map(lambda _: save(), range(2)))

    assert replay == saved
    assert saved.plan.payload == payload
    assert saved.plan.readable_markdown == markdown
    assert saved.setup_id is None
    assert "setup_id" not in saved.catalog_payload()
    assert saved.catalog_payload()["ruleset"] == payload["bindings"]["ruleset"]
    assert saved.catalog_payload()["campaign_setting"] == payload["bindings"][
        "campaign_setting"
    ]
    assert load_reusable_campaign_plan(
        tmp_path,
        saved.artifact_id,
        validate_plan=_validate_signal_plan,
    ) == saved
    assert reusable_campaign_plan_catalog(
        tmp_path,
        validate_plan=_validate_signal_plan,
    ) == (saved,)
    assert len(tuple((tmp_path / "campaign-plans").iterdir())) == 1

    record = json.loads(
        (tmp_path / "campaign-plans" / saved.artifact_id / "record.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["format_version"] == 2
    assert record["setup_id"] is None
    with pytest.raises(ValueError, match="bindings do not match"):
        load_reusable_campaign_plan(
            tmp_path,
            saved.artifact_id,
            validate_plan=_validate_wrong_ruleset,
        )


def test_rift_save_keeps_generalization_and_setup_routing(tmp_path: Path) -> None:
    source = bundled_rift_campaign_plan()
    payload = json.loads(canonical_json_bytes(source.payload))
    payload["boundaries"].append("Player Boundary: remove this from the sibling")
    markdown = source.readable_markdown + "\n\n## Selected campaign party\n\nPrivate party.\n"

    saved = save_reusable_rift_plan(
        tmp_path,
        operation_id="publish-rift-plan",
        title="Reusable Rift Route",
        source_payload=payload,
        source_markdown=markdown,
    )

    assert saved.setup_id == "the-rift-awakening"
    assert saved.catalog_payload()["setup_id"] == "the-rift-awakening"
    assert saved.plan.payload["starting_party"] == source.payload["starting_party"]
    assert "Player Boundary: remove this from the sibling" not in saved.plan.payload["boundaries"]
    assert "Private party." not in saved.plan.readable_markdown
    record_path = tmp_path / "campaign-plans" / saved.artifact_id / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["format_version"] == 2
    assert record["setup_id"] == "the-rift-awakening"

    record["format_version"] = 1
    record.pop("setup_id")
    record_path.write_bytes(canonical_json_bytes(record))
    assert load_reusable_campaign_plan(tmp_path, saved.artifact_id) == saved
    assert reusable_campaign_plan_catalog(tmp_path) == (saved,)
