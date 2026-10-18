from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from dataclasses import fields
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, SchemaIdentityError
from sylvia_gm.domain.model_capability import (
    CAPABILITY_TIERS,
    MODEL_CAPABILITY_REGISTRY_VERSION,
    ModelCapabilityPlacement,
    ModelCapabilityRegistry,
    ModelGuidanceStatus,
    model_guidance_from_payload,
    model_guidance_payload,
    model_guidance_payload_sha256,
)
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    content_root_for_record,
    manifest_from_record,
)

ROOT = Path(__file__).parents[1]


def _placement(model_id: str, tier_id: str, suffix: str) -> ModelCapabilityPlacement:
    return ModelCapabilityPlacement(
        model_id=model_id,
        tier_id=tier_id,
        benchmark_suite_id="sylvia.capability.alpha.v1",
        benchmark_suite_version="1",
        benchmark_suite_sha256=(suffix * 64)[:64],
        result_sha256=(("f" if suffix != "f" else "e") * 64),
    )


def _guidance_archive(minimum_tier_id: str) -> bytes:
    payload = b"{}"
    draft: dict[str, object] = {
        "id": "synthetic.model-guidance",
        "version": "1",
        "role": "primary",
        "metadata": {"label": "Synthetic Model Guidance"},
        "provenance": {"publisher": "synthetic-alpha-07", "source_sha256": "a" * 64},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": minimum_tier_id,
        "dependencies": [],
        "capabilities": [],
        "tools": [],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": "data/guidance.json",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    record = {**draft, "content_sha256": content_root_for_record(draft)}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 1, "packages": [record]}))
        archive.writestr("packages/synthetic.model-guidance/data/guidance.json", payload)
    return stream.getvalue()


def _prepare_ruleset_handoff(owner: AuthorityOwner, *, suffix: str) -> dict[str, object]:
    campaign_id = f"campaign-guidance-{suffix}"
    turn_id = f"turn-guidance-{suffix}"
    builder_session_id = f"session-builder-guidance-{suffix}"
    candidate = owner.install_package_archive(
        io.BytesIO(_guidance_archive("sylvia.capability.t04")),
        install_id=f"install-guidance-{suffix}",
    )
    owner.create_campaign(
        CampaignCreate(
            display_name=f"Guidance {suffix}",
            writer=WriterRole.BUILDER,
            package_lock=candidate.package_lock,
            turn_id=turn_id,
            campaign_id=campaign_id,
        )
    )
    owner.create_agent_session(
        campaign_id=campaign_id,
        role=WriterRole.BUILDER,
        provider_id="synthetic-builder-provider",
        model_id="synthetic-builder-model",
        session_id=builder_session_id,
        selected_skill="BUILD CAMPAIGN PLAN",
    )
    evidence_id = f"evidence-guidance-{suffix}"
    owner.record_builder_evidence(
        session_id=builder_session_id,
        evidence_id=evidence_id,
        source_kind="user_provided",
        content_sha256="b" * 64,
        source_locator=None,
        safe_excerpt="Synthetic Alpha-07 plan evidence.",
        claims=("guidance-ready",),
    )
    content = {"title": "Guidance plan", "facts": ["guidance-ready"]}
    content_sha256 = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
    lock_id = owner.current_package_lock(campaign_id).lock_id
    plan = owner.admit_campaign_plan(
        plan_id=f"plan-guidance-{suffix}",
        campaign_id=campaign_id,
        builder_session_id=builder_session_id,
        expected_revision=0,
        expected_package_lock_id=lock_id,
        content=content,
        content_sha256=content_sha256,
        provenance_evidence_ids=(evidence_id,),
        validation_outcome="valid",
        acceptance_status="accepted",
    )
    owner.complete_turn(campaign_id, turn_id)
    return {
        "handoff_id": f"handoff-guidance-{suffix}",
        "campaign_id": campaign_id,
        "builder_session_id": builder_session_id,
        "dm_session_id": f"session-dm-guidance-{suffix}",
        "dm_provider_id": "synthetic-dm-provider",
        "dm_model_id": "synthetic/dm-model",
        "expected_writer": WriterRole.BUILDER,
        "expected_revision": 1,
        "expected_package_lock_id": lock_id,
        "expected_package_candidate_id": candidate.candidate_id,
        "plan_id": plan.plan_id,
        "expected_plan_revision": plan.plan_revision,
        "expected_plan_content_sha256": plan.content_sha256,
        "completed_turn_id": turn_id,
    }


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("new_provider", ["synthetic-dm-provider", "another-provider"])
def test_gm_model_switch_preserves_handoff_and_allows_new_interview(
    tmp_path: Path, legacy: bool, new_provider: str,
) -> None:
    """Changing today's model must not invalidate the original campaign opening."""
    database = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(database, "owner-switch-prepare") as owner:
        request = _prepare_ruleset_handoff(owner, suffix="switch")
        handoff = owner.handoff_builder_to_dm(**request, model_guidance_override=True)
    if legacy:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT safe_payload_json FROM builder_dm_handoffs WHERE handoff_id = ?",
                (handoff.handoff_id,),
            ).fetchone()
            payload = json.loads(row[0])
            payload.pop("dm_provider_id", None)
            connection.execute(
                "UPDATE builder_dm_handoffs SET safe_payload_json = ? WHERE handoff_id = ?",
                (canonical_json_bytes(payload).decode("utf-8"), handoff.handoff_id),
            )
    with AuthorityOwner.open(database, "owner-switch-current") as owner:
        owner.update_agent_session_model(
            handoff.dm_session_id, provider_id=new_provider, model_id="another/model",
        )
        draft = owner.create_guided_campaign_draft(
            operation_id="new-interview-after-switch", display_name="Another campaign",
            builder_session_id="another-interview", builder_provider_id="default-provider",
            builder_model_id="default/model",
        )
        assert draft.builder_session_id == "another-interview"
        assert owner.builder_dm_handoff(handoff.handoff_id) == handoff
    with AuthorityOwner.open(database, "owner-switch-reopen") as owner:
        session = owner.session_context(handoff.dm_session_id).session
        assert (session.provider_id, session.model_id) == (new_provider, "another/model")
        assert owner.builder_dm_handoff(handoff.handoff_id) == handoff
        assert owner.session_context("another-interview").session.model_id == "default/model"

    # Capturing the opening provider must not weaken its existing digest check.
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT safe_payload_json FROM builder_dm_handoffs WHERE handoff_id = ?",
            (handoff.handoff_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["dm_provider_id"] = "tampered-opening-provider"
        connection.execute(
            "UPDATE builder_dm_handoffs SET safe_payload_json = ? WHERE handoff_id = ?",
            (canonical_json_bytes(payload).decode("utf-8"), handoff.handoff_id),
        )
    with pytest.raises(SchemaIdentityError):
        AuthorityOwner.open(database, "owner-reject-tampered-opening")


def test_legacy_handoff_reopens_after_an_already_saved_same_provider_switch(tmp_path: Path) -> None:
    """An old build's model switch remains recoverable without rewriting campaign data."""
    database = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(database, "owner-legacy-switch") as owner:
        request = _prepare_ruleset_handoff(owner, suffix="legacy-switched")
        handoff = owner.handoff_builder_to_dm(**request, model_guidance_override=True)
        owner.update_agent_session_model(
            handoff.dm_session_id, provider_id="synthetic-dm-provider", model_id="another/model",
        )
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT safe_payload_json FROM builder_dm_handoffs WHERE handoff_id = ?",
            (handoff.handoff_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload.pop("dm_provider_id")
        connection.execute(
            "UPDATE builder_dm_handoffs SET safe_payload_json = ? WHERE handoff_id = ?",
            (canonical_json_bytes(payload).decode("utf-8"), handoff.handoff_id),
        )
    with AuthorityOwner.open(database, "owner-legacy-switch-reopened") as owner:
        assert owner.session_context(handoff.dm_session_id).session.model_id == "another/model"
        assert owner.builder_dm_handoff(handoff.handoff_id) == handoff


def test_alpha_07_registry_has_ten_stable_ordered_neutral_tiers() -> None:
    assert MODEL_CAPABILITY_REGISTRY_VERSION == "sylvia.capability.registry.v1"
    assert len(CAPABILITY_TIERS) == 10
    assert tuple(tier.rank for tier in CAPABILITY_TIERS) == tuple(range(10))
    assert tuple(tier.tier_id for tier in CAPABILITY_TIERS) == tuple(
        f"sylvia.capability.t{rank:02d}" for rank in range(10)
    )
    assert tuple(tier.label for tier in CAPABILITY_TIERS) == tuple(
        f"Capability tier {rank}" for rank in range(10)
    )


def test_alpha_07_placement_is_exact_empirical_evidence_without_forbidden_inputs() -> None:
    placement_fields = {field.name for field in fields(ModelCapabilityPlacement)}
    assert placement_fields == {
        "model_id",
        "tier_id",
        "benchmark_suite_id",
        "benchmark_suite_version",
        "benchmark_suite_sha256",
        "result_sha256",
    }
    assert placement_fields.isdisjoint(
        {
            "provider_id",
            "provider",
            "price",
            "cost",
            "parameter_count",
            "parameters",
            "marketing_label",
            "recommended",
            "premium",
        }
    )

    registry = ModelCapabilityRegistry(
        (_placement("configured/model-a", "sylvia.capability.t04", "a"),)
    )
    assert registry.placement("configured/model-a") is not None
    assert registry.placement("configured/model-a").tier_id == "sylvia.capability.t04"
    assert registry.placement("configured/model-b") is None

    with pytest.raises(ValueError, match="tier"):
        _placement("configured/model-a", "sylvia.capability.t99", "a")
    with pytest.raises(ValueError, match="duplicate"):
        ModelCapabilityRegistry(
            (
                _placement("configured/model-a", "sylvia.capability.t04", "a"),
                _placement("configured/model-a", "sylvia.capability.t05", "b"),
            )
        )


@pytest.mark.parametrize(
    ("model_tier", "minimum_tier", "status", "warning_required"),
    (
        ("sylvia.capability.t03", "sylvia.capability.t04", ModelGuidanceStatus.BELOW, True),
        ("sylvia.capability.t04", "sylvia.capability.t04", ModelGuidanceStatus.MEETS, False),
        ("sylvia.capability.t07", "sylvia.capability.t04", ModelGuidanceStatus.MEETS, False),
    ),
)
def test_alpha_07_compares_empirical_tiers_and_never_hard_gates(
    model_tier: str,
    minimum_tier: str,
    status: ModelGuidanceStatus,
    warning_required: bool,
) -> None:
    registry = ModelCapabilityRegistry((_placement("configured/model", model_tier, "a"),))

    initial = registry.assess(
        model_id="configured/model",
        minimum_tier_id=minimum_tier,
        override_accepted=False,
    )
    assert initial.status is status
    assert initial.warning_required is warning_required
    assert initial.may_continue is not warning_required

    overridden = registry.assess(
        model_id="configured/model",
        minimum_tier_id=minimum_tier,
        override_accepted=True,
    )
    assert overridden.status is status
    assert overridden.warning_required is warning_required
    assert overridden.may_continue
    assert overridden.override_accepted is warning_required
    assert overridden.warning_code == ("model_below_ruleset_minimum" if warning_required else None)


def test_alpha_07_unrated_warns_and_no_rules_is_genuinely_not_applicable() -> None:
    registry = ModelCapabilityRegistry(())

    unrated = registry.assess(
        model_id="configured/unrated-model",
        minimum_tier_id="sylvia.capability.t02",
        override_accepted=False,
    )
    assert unrated.status is ModelGuidanceStatus.UNRATED
    assert unrated.warning_required
    assert not unrated.may_continue
    assert unrated.warning_code == "model_capability_unrated"

    no_rules = registry.assess(
        model_id="configured/unrated-model",
        minimum_tier_id=None,
        override_accepted=False,
    )
    assert no_rules.status is ModelGuidanceStatus.NOT_APPLICABLE
    assert no_rules.minimum_tier_id is None
    assert no_rules.placement_tier_id is None
    assert no_rules.placement_evidence_sha256 is None
    assert not no_rules.warning_required
    assert no_rules.may_continue


def test_alpha_07_decision_identity_binds_model_requirement_evidence_and_override() -> None:
    registry = ModelCapabilityRegistry(
        (_placement("configured/model", "sylvia.capability.t01", "a"),)
    )
    decision = registry.assess(
        model_id="configured/model",
        minimum_tier_id="sylvia.capability.t05",
        override_accepted=True,
    )
    digest = model_guidance_payload_sha256(decision)
    assert len(digest) == 64
    assert digest == model_guidance_payload_sha256(decision)

    changed = registry.assess(
        model_id="configured/model",
        minimum_tier_id="sylvia.capability.t05",
        override_accepted=False,
    )
    assert model_guidance_payload_sha256(changed) != digest

    payload = model_guidance_payload(decision)
    assert model_guidance_from_payload(payload) == decision
    with pytest.raises(ValueError, match="invalid"):
        model_guidance_from_payload({**payload, "price": 1})


def test_alpha_07_warning_override_and_exact_evidence_survive_reopen(tmp_path: Path) -> None:
    database = tmp_path / "model-guidance.db"
    portable = tmp_path / "model-guidance.sylvia-gm-campaign"
    registry = ModelCapabilityRegistry(
        (_placement("synthetic/dm-model", "sylvia.capability.t02", "a"),)
    )
    with AuthorityOwner.open(
        database,
        "owner-guidance-a",
        model_capability_registry=registry,
    ) as owner:
        request = _prepare_ruleset_handoff(owner, suffix="persist")
        campaign_id = str(request["campaign_id"])
        preview = owner.model_guidance(
            campaign_id=campaign_id,
            model_id="synthetic/dm-model",
        )
        assert preview.status is ModelGuidanceStatus.BELOW
        assert preview.warning_required and not preview.may_continue
        assert preview.minimum_tier_id == "sylvia.capability.t04"
        assert preview.placement_tier_id == "sylvia.capability.t02"
        assert preview.placement_evidence_sha256 is not None

        with pytest.raises(CommandRejected) as warning:
            owner.handoff_builder_to_dm(**request)
        assert warning.value.code == "model_below_ruleset_minimum"
        assert owner.snapshot(campaign_id).writer is WriterRole.BUILDER
        assert owner.snapshot(campaign_id).revision == 1

        handoff = owner.handoff_builder_to_dm(
            **request,
            model_guidance_override=True,
        )
        assert handoff.model_guidance == registry.assess(
            model_id="synthetic/dm-model",
            minimum_tier_id="sylvia.capability.t04",
            override_accepted=True,
        )
        assert handoff.model_guidance.override_accepted
        dm_index = next(
            session
            for entry in owner.campaign_index()
            if entry.campaign_id == campaign_id
            for session in entry.sessions
            if session.session_id == request["dm_session_id"]
        )
        assert dm_index.model_guidance == handoff.model_guidance
        owner.campaign_lifecycle(campaign_id)
        owner.export_campaign("export-model-guidance", campaign_id, portable)

    # Reopen with no live placement registry: the recorded decision and exact replay
    # remain durable rather than being silently reclassified by newer evidence.
    with AuthorityOwner.open(database, "owner-guidance-b") as reopened:
        stored = reopened.builder_dm_handoff(str(request["handoff_id"]))
        assert stored.model_guidance == handoff.model_guidance
        assert stored.model_guidance.override_accepted
        assert (
            reopened.handoff_builder_to_dm(
                **request,
                model_guidance_override=True,
            )
            == stored
        )
        dm_index = next(
            session
            for entry in reopened.campaign_index()
            if entry.campaign_id == request["campaign_id"]
            for session in entry.sessions
            if session.session_id == request["dm_session_id"]
        )
        assert dm_index.model_guidance == stored.model_guidance
        with pytest.raises(IdempotencyConflict):
            reopened.handoff_builder_to_dm(
                **{**request, "dm_model_id": "synthetic/changed-model"},
                model_guidance_override=True,
            )

    recovered_database = tmp_path / "recovered-model-guidance.db"
    with AuthorityOwner.open(recovered_database, "owner-guidance-recovered") as recovered:
        receipt = recovered.recover_campaign(
            recovery_id="recover-model-guidance",
            source=portable,
        )
        assert receipt.campaign_id == request["campaign_id"]
        recovered_handoff = recovered.builder_dm_handoff(str(request["handoff_id"]))
        assert recovered_handoff.model_guidance == handoff.model_guidance
        recovered_dm = next(
            session
            for entry in recovered.campaign_index()
            if entry.campaign_id == request["campaign_id"]
            for session in entry.sessions
            if session.session_id == request["dm_session_id"]
        )
        assert recovered_dm.model_guidance == handoff.model_guidance


def test_alpha_07_ruleset_minimum_is_required_valid_and_hash_bound() -> None:
    draft: dict[str, object] = {
        "id": "synthetic.guidance",
        "version": "1",
        "role": "primary",
        "metadata": {"label": "Synthetic guidance"},
        "provenance": {"publisher": "synthetic-tests", "source_sha256": "a" * 64},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t04",
        "dependencies": [],
        "capabilities": [],
        "tools": [],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [],
    }
    record = {**draft, "content_sha256": content_root_for_record(draft)}
    manifest = manifest_from_record(record, format_version=1)
    assert manifest.minimum_model_capability_tier == "sylvia.capability.t04"

    missing = dict(record)
    missing.pop("minimum_model_capability_tier")
    with pytest.raises(ValueError, match="portable format"):
        manifest_from_record(missing, format_version=1)

    unsupported = {**draft, "minimum_model_capability_tier": "sylvia.capability.t99"}
    unsupported["content_sha256"] = content_root_for_record(unsupported)
    with pytest.raises(ValueError, match="tier"):
        manifest_from_record(unsupported, format_version=1)


def test_alpha_07_bundled_rulesets_declare_explicit_minimums() -> None:
    dnd = json.loads(
        (ROOT / "rulesets" / "srd-lite" / "package-record.json").read_text(encoding="utf-8")
    )
    signal = json.loads(
        (
            ROOT / "tests" / "fixtures" / "rulesets" / "signal-and-static" / "package-record.json"
        ).read_text(encoding="utf-8")
    )
    assert dnd["minimum_model_capability_tier"] == "sylvia.capability.t06"
    assert signal["minimum_model_capability_tier"] == "sylvia.capability.t02"
