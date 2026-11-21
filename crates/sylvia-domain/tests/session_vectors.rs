//! Compare sylvia_domain::session and ::model_capability with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_session_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use sylvia_base::pyjson;
use sylvia_base::Json;
use sylvia_domain::authority::WriterRole;
use sylvia_domain::model_capability::{
    model_guidance_from_payload, model_guidance_payload, model_guidance_payload_sha256,
    ModelCapabilityPlacement, ModelCapabilityRegistry,
};
use sylvia_domain::session::{
    agent_session_payload_sha256, builder_chat_payload_sha256, builder_dm_handoff_payload_sha256,
    campaign_plan_payload_sha256, BuilderChatRecord, BuilderDMHandoff, BuilderDMHandoffFields,
    BuilderDMHandoffRequest, BuilderEvidence, CampaignPlan, CampaignPlanFields, CampaignPlanRequest,
    ChildEvidence, EvidenceSourceKind, PlanAcceptanceStatus, PlanValidationOutcome, SessionMessage,
    SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus,
};
use sylvia_domain::DomainResult;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/session_vectors.json")).expect("vector file is valid JSON")
}

fn outcome<T>(result: DomainResult<T>, project: impl Fn(T) -> Json) -> Json {
    match result {
        Ok(value) => json!(["ok", project(value)]),
        Err(error) => json!([error.python_class(), error.message()]),
    }
}

fn text(value: &Json) -> String {
    value.as_str().expect("text").to_owned()
}

fn optional(value: &Json) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn strings(value: &Json) -> Vec<String> {
    value.as_array().unwrap().iter().map(text).collect()
}

fn check(name: &str, cases: &[Json], field: &str, mut run: impl FnMut(&Json) -> Json) {
    let mut failures = Vec::new();
    for case in cases {
        let actual = run(case);
        if actual != case[field] {
            failures.push(format!("{case}: rust {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{name}: {} of {} differ, first: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(3)]
    );
}

fn list<'a>(vectors: &'a Json, name: &str) -> &'a [Json] {
    vectors[name].as_array().unwrap()
}

fn session(fields: &Json) -> DomainResult<SessionRecord> {
    SessionRecord::new(SessionRecordFields {
        session_id: text(&fields["session_id"]),
        campaign_id: optional(&fields["campaign_id"]),
        role: WriterRole::parse(fields["role"].as_str().unwrap()).unwrap(),
        provider_id: text(&fields["provider_id"]),
        model_id: text(&fields["model_id"]),
        status: SessionStatus::parse(fields["status"].as_str().unwrap()).unwrap(),
        parent_session_id: optional(&fields["parent_session_id"]),
        selected_skill: optional(&fields["selected_skill"]),
        scope: strings(&fields["scope"]),
        reasoning_effort: optional(&fields["reasoning_effort"]),
        reasoning_effort_once: optional(&fields["reasoning_effort_once"]),
    })
}

#[test]
fn guidance_decisions_match_python() {
    let vectors = vectors();
    check("guidance", list(&vectors, "guidance"), "outcome", |case| {
        outcome(model_guidance_from_payload(&case["payload"]), |decision| {
            json!([
                model_guidance_payload_sha256(&decision),
                Json::Object(model_guidance_payload(&decision)),
                decision.warning_required(),
                decision.may_continue()
            ])
        })
    });
}

#[test]
fn placements_and_registry_match_python() {
    let vectors = vectors();
    check("placements", list(&vectors, "placements"), "outcome", |case| {
        let f = &case["fields"];
        let t = |k: &str| f[k].as_str().unwrap().to_owned();
        outcome(
            ModelCapabilityPlacement::new(
                &t("model_id"),
                &t("tier_id"),
                &t("benchmark_suite_id"),
                &t("benchmark_suite_version"),
                &t("benchmark_suite_sha256"),
                &t("result_sha256"),
            ),
            |placement| Json::from(placement.evidence_sha256()),
        )
    });
    let sha = "a".repeat(64);
    let placements = vec![
        ModelCapabilityPlacement::new(
            "model-a",
            "sylvia.capability.t02",
            "suite",
            "1",
            &sha,
            &"b".repeat(64),
        )
        .unwrap(),
        ModelCapabilityPlacement::new(
            "model-b",
            "sylvia.capability.t07",
            "suite",
            "1",
            &sha,
            &"c".repeat(64),
        )
        .unwrap(),
    ];
    let registry = ModelCapabilityRegistry::new(placements.clone()).unwrap();
    check("assess", vectors["assess"]["cases"].as_array().unwrap(), "outcome", |case| {
        outcome(
            registry.assess(
                case["model_id"].as_str().unwrap(),
                case["minimum"].as_str(),
                case["override"].as_bool().unwrap(),
            ),
            |decision| {
                json!([
                    model_guidance_payload_sha256(&decision),
                    Json::Object(model_guidance_payload(&decision))
                ])
            },
        )
    });
    let duplicate =
        outcome(ModelCapabilityRegistry::new(vec![placements[0].clone(), placements[0].clone()]), |_| {
            Json::Null
        });
    assert_eq!(duplicate, vectors["assess"]["duplicate"]);
}

#[test]
fn session_records_match_python() {
    let vectors = vectors();
    check("sessions", list(&vectors, "sessions"), "outcome", |case| {
        outcome(session(&case["fields"]), |record| Json::from(agent_session_payload_sha256(&record)))
    });
}

#[test]
fn handoffs_match_python() {
    let vectors = vectors();
    let guidance = model_guidance_from_payload(&vectors["handoff_guidance"]).unwrap();
    check("handoffs", list(&vectors, "handoffs"), "outcome", |case| {
        let f = &case["fields"];
        let int = |k: &str| f[k].as_i64().unwrap();
        outcome(
            BuilderDMHandoff::new(BuilderDMHandoffFields {
                handoff_id: text(&f["handoff_id"]),
                campaign_id: text(&f["campaign_id"]),
                builder_session_id: text(&f["builder_session_id"]),
                dm_session_id: text(&f["dm_session_id"]),
                package_lock_id: text(&f["package_lock_id"]),
                package_candidate_id: optional(&f["package_candidate_id"]),
                plan_id: text(&f["plan_id"]),
                plan_revision: int("plan_revision"),
                plan_content_sha256: text(&f["plan_content_sha256"]),
                expected_revision: int("expected_revision"),
                resulting_campaign_revision: int("resulting_campaign_revision"),
                chronology_ordinal: int("chronology_ordinal"),
                completed_turn_id: text(&f["completed_turn_id"]),
                model_guidance: guidance.clone(),
                payload_sha256: text(&f["payload_sha256"]),
            }),
            |_| Json::Null,
        )
    });
    check("handoff payloads", list(&vectors, "handoff_payloads"), "sha256", |case| {
        let r = &case["request"];
        Json::from(builder_dm_handoff_payload_sha256(&BuilderDMHandoffRequest {
            handoff_id: text(&r["handoff_id"]),
            campaign_id: text(&r["campaign_id"]),
            builder_session_id: text(&r["builder_session_id"]),
            dm_session_id: text(&r["dm_session_id"]),
            dm_provider_id: text(&r["dm_provider_id"]),
            dm_model_id: text(&r["dm_model_id"]),
            expected_writer: WriterRole::parse(r["expected_writer"].as_str().unwrap()).unwrap(),
            expected_revision: r["expected_revision"].as_i64().unwrap(),
            expected_package_lock_id: text(&r["expected_package_lock_id"]),
            expected_package_candidate_id: optional(&r["expected_package_candidate_id"]),
            plan_id: text(&r["plan_id"]),
            expected_plan_revision: r["expected_plan_revision"].as_i64().unwrap(),
            expected_plan_content_sha256: text(&r["expected_plan_content_sha256"]),
            completed_turn_id: text(&r["completed_turn_id"]),
            model_guidance_sha256: text(&r["model_guidance_sha256"]),
        }))
    });
}

#[test]
fn campaign_plans_match_python() {
    let vectors = vectors();
    let fields = |f: &Json| CampaignPlanFields {
        plan_id: text(&f["plan_id"]),
        campaign_id: text(&f["campaign_id"]),
        package_lock_id: text(&f["package_lock_id"]),
        source_session_id: text(&f["source_session_id"]),
        content_sha256: text(&f["content_sha256"]),
        content: f["content"].clone(),
        provenance_evidence_ids: strings(&f["provenance_evidence_ids"]),
        validation_outcome: PlanValidationOutcome::Valid,
        acceptance_status: PlanAcceptanceStatus::Accepted,
        plan_revision: f["plan_revision"].as_i64().unwrap(),
        resulting_campaign_revision: f["resulting_campaign_revision"].as_i64().unwrap(),
        payload_sha256: text(&f["payload_sha256"]),
    };
    check("plans", list(&vectors, "plans"), "outcome", |case| {
        outcome(CampaignPlan::new(fields(&case["fields"])), |_| Json::Null)
    });
    check("plan payloads", list(&vectors, "plans"), "payload", |case| {
        let f = &case["fields"];
        outcome(
            campaign_plan_payload_sha256(&CampaignPlanRequest {
                plan_id: text(&f["plan_id"]),
                campaign_id: text(&f["campaign_id"]),
                source_session_id: text(&f["source_session_id"]),
                expected_revision: f["plan_revision"].as_i64().unwrap(),
                expected_package_lock_id: text(&f["package_lock_id"]),
                content_sha256: text(&f["content_sha256"]),
                content: f["content"].clone(),
                provenance_evidence_ids: strings(&f["provenance_evidence_ids"]),
                validation_outcome: PlanValidationOutcome::Valid,
                acceptance_status: PlanAcceptanceStatus::Accepted,
            }),
            Json::from,
        )
    });
}

#[test]
fn evidence_messages_and_chats_match_python() {
    let vectors = vectors();
    check("builder evidence", list(&vectors, "builder_evidence"), "outcome", |case| {
        let f = &case["fields"];
        let evidence = BuilderEvidence {
            evidence_id: text(&f["evidence_id"]),
            source_kind: EvidenceSourceKind::parse(f["source_kind"].as_str().unwrap()).unwrap(),
            content_sha256: text(&f["content_sha256"]),
            source_locator: optional(&f["source_locator"]),
            safe_excerpt: text(&f["safe_excerpt"]),
            claims: strings(&f["claims"]),
        };
        outcome(evidence.validated(), |e| Json::Object(e.payload()))
    });
    check("child evidence", list(&vectors, "child_evidence"), "outcome", |case| {
        let f = &case["fields"];
        let evidence = ChildEvidence {
            evidence_id: text(&f["evidence_id"]),
            child_session_id: text(&f["child_session_id"]),
            parent_session_id: text(&f["parent_session_id"]),
            content_sha256: text(&f["content_sha256"]),
            safe_summary: text(&f["safe_summary"]),
            claims: strings(&f["claims"]),
            authoritative: f["authoritative"].as_bool().unwrap(),
        };
        outcome(evidence.validated(), |e| Json::Object(e.payload()))
    });
    check("messages", list(&vectors, "messages"), "outcome", |case| {
        let f = &case["fields"];
        outcome(
            SessionMessage::new(
                text(&f["session_id"]),
                f["sequence"].as_i64().unwrap(),
                text(&f["message_id"]),
                SessionMessageKind::parse(f["kind"].as_str().unwrap()).unwrap(),
                &f["content"],
                f["canonical_sequence"].as_i64(),
            ),
            |_| Json::Null,
        )
    });
    let sessions = vectors["chats"]["sessions"].as_array().unwrap();
    check("chats", vectors["chats"]["cases"].as_array().unwrap(), "outcome", |case| {
        let record = session(&sessions[case["session"].as_u64().unwrap() as usize]).and_then(|session| {
            BuilderChatRecord::new(
                session,
                text(&case["display_name"]),
                case["created_ordinal"].as_i64().unwrap(),
            )
        });
        outcome(record, |record| Json::from(builder_chat_payload_sha256(&record)))
    });
}
