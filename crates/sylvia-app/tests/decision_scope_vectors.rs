//! Compare sylvia-app::decision_scope and sylvia-app::actor_decisions with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_decision_scope_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value as Json};
use sylvia_app::actor_decisions::{project_actor_decision, public_actor_observation, ActorDecisionRequest};
use sylvia_app::decision_scope::{
    player_viewer_ids, project_campaign_preparation_scope, project_model_decision_scope, project_player_control_scope,
};
use sylvia_domain::authority::{
    CampaignState, LifecycleState, ModelDecisionBoundary, PackageLock, PackageLockEntry, PackageRole, Participant,
    ParticipantFields, WriterRole,
};
use sylvia_domain::error::DomainError;

fn campaign(case: &Json) -> CampaignState {
    let participants = case["participants"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| {
            Participant::new(ParticipantFields {
                participant_id: p["participant_id"].as_str().unwrap().into(),
                kind: p["kind"].as_str().unwrap().into(),
                package_id: p["package_id"].as_str().unwrap().into(),
                package_version: "1.0.0".into(),
                data: p["data"].as_object().unwrap().clone(),
                package_sha256: None,
                schema_id: None,
                schema_sha256: None,
                validation_status: "quarantined".into(),
                adaptation_id: None,
            })
            .expect("a valid participant")
        })
        .collect();
    let entries = case["package_lock"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| {
            PackageLockEntry::new(
                e["package_id"].as_str().unwrap(),
                e["version"].as_str().unwrap(),
                e["sha256"].as_str().unwrap(),
                PackageRole::Primary,
                0,
                Vec::new(),
                Vec::new(),
            )
            .unwrap()
        })
        .collect();
    CampaignState {
        campaign_id: case["campaign_id"].as_str().unwrap().into(),
        display_name: "Campaign".into(),
        lifecycle: LifecycleState::Active,
        writer: WriterRole::Dm,
        revision: case["revision"].as_i64().unwrap(),
        package_lock: PackageLock::new(entries).unwrap(),
        state: case["state"].as_object().unwrap().clone(),
        participants,
    }
}

fn error(error: &DomainError) -> Json {
    let class = match error {
        DomainError::Value(_) => "ValueError",
        DomainError::Type(_) => "TypeError",
        DomainError::Diagnosed { error_type, .. }
        | DomainError::Rejected { error_type, .. }
        | DomainError::Uncaught { error_type, .. } => error_type,
    };
    json!({"error": [class, error.to_string()]})
}

fn boundary_json(boundary: &ModelDecisionBoundary) -> Json {
    json!({
        "owner": boundary.owner.as_str(), "actor_id": boundary.actor_id, "actor_name": boundary.actor_name,
        "actor_state": boundary.actor_state, "campaign_revision": boundary.campaign_revision,
        "active_side_id": boundary.active_side_id, "focused_actor_id": boundary.focused_actor_id,
        "eligible_actors": boundary.eligible_actors.iter().map(|actor| json!({
            "actor_id": actor.actor_id, "actor_name": actor.actor_name, "actor_state": actor.actor_state,
        })).collect::<Vec<_>>(),
        "preparation": boundary.preparation,
    })
}

fn decision_json(request: &ActorDecisionRequest) -> Json {
    json!({
        "campaign_id": request.campaign_id, "turn_id": request.turn_id, "package_id": request.package_id,
        "package_version": request.package_version, "package_sha256": request.package_sha256,
        "actor_id": request.actor_id, "actor_name": request.actor_name, "revision": request.revision,
        "decision_id": request.decision_id, "instructions": request.instructions, "persona": request.persona,
        "observation": request.observation,
        "actions": request.actions.iter().map(|action| json!({
            "identity": action.identity, "label": action.label, "operation": action.operation, "arguments": action.arguments,
        })).collect::<Vec<_>>(),
        "observation_payload": request.observation_payload(),
    })
}

fn optional<T>(result: Result<Option<T>, DomainError>, show: impl Fn(&T) -> Json) -> Json {
    match result {
        Ok(Some(value)) => json!({"result": show(&value)}),
        Ok(None) => json!({"result": null}),
        Err(failure) => error(&failure),
    }
}

#[test]
fn decision_scopes_project_as_python() {
    let vectors: Json = serde_json::from_str(include_str!("vectors/decision_scope_vectors.json")).expect("valid JSON");
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() >= 3000);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let snapshot = campaign(case);
        let declared = case["declared"].as_object().unwrap();
        let tools = case["tools"].as_object().unwrap();
        let boundary = project_model_decision_scope(&snapshot, declared).ok().flatten();
        let actual = json!({
            "scope": optional(project_model_decision_scope(&snapshot, declared), boundary_json),
            "preparation": optional(project_campaign_preparation_scope(&snapshot, declared), boundary_json),
            "control": match project_player_control_scope(&snapshot, declared) {
                Ok(scope) => json!({"result": scope}),
                Err(failure) => error(&failure),
            },
            "viewers": json!({"result": player_viewer_ids(&snapshot, Some(declared)).into_iter().collect::<Vec<_>>()}),
            "public": match public_actor_observation(&snapshot, declared) {
                Ok(view) => json!({"result": view}),
                Err(failure) => error(&failure),
            },
            "decision": optional(project_actor_decision(&snapshot, boundary.as_ref(), declared, "turn-7", tools), decision_json),
        });
        for key in ["scope", "preparation", "control", "viewers", "public", "decision"] {
            let (a, w) = (serde_json::to_string(&actual[key]).unwrap(), serde_json::to_string(&case[key]).unwrap());
            if a != w {
                failures.push(format!("case {index} {key}\n  python {w}\n  rust   {a}"));
            }
        }
    }
    assert!(failures.is_empty(), "{} differ, first: {:#?}", failures.len(), &failures[..failures.len().min(5)]);
}
