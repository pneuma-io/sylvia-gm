//! Campaign snapshots against Python (`tests/vectors/snapshots/snapshot_runs.json`, written by
//! `generate_snapshot_runs.py`): every campaign of eight Python-made profiles read with
//! `snapshot`, `model_context_snapshot` and six display queries, plus a missing campaign. Each
//! read must be Python's value field for field, in Python's order, or Python's refusal. The two
//! Riichi campaigns of the participant profile declare engine-private state and a decision
//! scope, so their `model_context_snapshot` hides that state and projects the boundary through
//! lane 2's `decision_scope`, as Python does.

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::snapshots;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    ActionRecord, CampaignContinuityView, CampaignState, CanonicalEvent, ConsequenceRecord, MutationRecord,
    PackageLock, Participant, TurnStatus,
};
use sylvia_store::snapshots::DisplayQuery;
use sylvia_store::{AuthorityStore, StoreHandle, StoreResult};

fn base(name: &str) -> Vec<u8> {
    let relative = match name {
        "session_pages" => "session_pages/seed.sqlite3.gz",
        "failed" => "stored_authority/failed.sqlite3.gz",
        "cast" => "stored_authority/cast.sqlite3.gz",
        "archived" => "stored_authority/archived.sqlite3.gz",
        "trashed" => "stored_authority/trashed.sqlite3.gz",
        "campaigns" => "campaigns/seed.sqlite3.gz",
        "continuity" => "continuity/batch_and_view.sqlite3.gz",
        "v4" => "packages/participants_v4.sqlite3.gz",
        // The generator's own base: the cast base with a Riichi game started on `campaign-table`.
        "riichi_started" => "../../../sylvia-app/tests/vectors/snapshots/riichi_started.sqlite3.gz",
        other => panic!("unknown base {other}"),
    };
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../sylvia-store/tests/vectors").join(relative);
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

/// A dataclass as the generator writes it: `__class__`, then the fields in order.
fn object(class: &str, fields: Vec<(&str, Json)>) -> Json {
    let mut map = JsonMap::new();
    map.insert("__class__".into(), class.into());
    for (name, value) in fields {
        map.insert(name.into(), value);
    }
    Json::Object(map)
}

fn optional(value: Option<&str>) -> Json {
    value.map_or(Json::Null, Json::from)
}

fn lock(lock: &PackageLock) -> Json {
    let entries = lock
        .entries()
        .iter()
        .map(|entry| {
            let dependencies = entry
                .dependencies()
                .iter()
                .map(|dependency| {
                    object(
                        "PackageDependency",
                        vec![
                            ("package_id", dependency.package_id().into()),
                            ("version", dependency.version().into()),
                            ("sha256", dependency.sha256().into()),
                            ("kind", dependency.kind().into()),
                            ("namespace", dependency.namespace().into()),
                        ],
                    )
                })
                .collect();
            object(
                "PackageLockEntry",
                vec![
                    ("package_id", entry.package_id().into()),
                    ("version", entry.version().into()),
                    ("sha256", entry.sha256().into()),
                    ("role", entry.role().as_str().into()),
                    ("load_order", entry.load_order().into()),
                    ("dependencies", Json::Array(dependencies)),
                    (
                        "capabilities",
                        entry.capabilities().iter().map(|item| Json::from(item.as_str())).collect(),
                    ),
                ],
            )
        })
        .collect();
    object("PackageLock", vec![("entries", Json::Array(entries))])
}

fn participant(participant: &Participant) -> Json {
    // Python reads an adapted participant at schema 12 as `ParticipantArtifact`.
    let class = if participant.adaptation_id().is_some() { "ParticipantArtifact" } else { "Participant" };
    object(
        class,
        vec![
            ("participant_id", participant.participant_id().into()),
            ("kind", participant.kind().into()),
            ("package_id", participant.package_id().into()),
            ("package_version", participant.package_version().into()),
            ("data", Json::Object(participant.data().clone())),
            ("package_sha256", optional(participant.package_sha256())),
            ("schema_id", optional(participant.schema_id())),
            ("schema_sha256", optional(participant.schema_sha256())),
            ("validation_status", participant.validation_status().as_str().into()),
            ("adaptation_id", optional(participant.adaptation_id())),
        ],
    )
}

fn action(action: &ActionRecord) -> Json {
    object(
        "ActionRecord",
        vec![
            ("action_id", action.action_id.as_str().into()),
            ("turn_id", action.turn_id.as_str().into()),
            ("status", action.status.as_str().into()),
            ("expected_revision", action.expected_revision.into()),
            ("resulting_revision", action.resulting_revision.into()),
            ("writer", action.writer.as_str().into()),
            ("package_id", action.package_id.as_str().into()),
            ("package_version", action.package_version.as_str().into()),
            ("package_sha256", action.package_sha256.as_str().into()),
            ("tool_name", action.tool_name.as_str().into()),
            ("parameters", Json::Object(action.parameters.clone())),
            ("result", Json::Object(action.result.clone())),
        ],
    )
}

fn event(event: &CanonicalEvent) -> Json {
    object(
        "CanonicalEvent",
        vec![
            ("sequence", event.sequence.into()),
            ("event_type", event.event_type.as_str().into()),
            ("turn_id", event.turn_id.as_str().into()),
            ("action_id", optional(event.action_id.as_deref())),
            ("safe_payload", Json::Object(event.safe_payload.clone())),
            ("visibility", event.visibility.as_str().into()),
            ("attempt_id", optional(event.attempt_id.as_deref())),
            ("attempt_ordinal", event.attempt_ordinal.map_or(Json::Null, Json::from)),
        ],
    )
}

fn mutation(mutation: &MutationRecord) -> Json {
    object(
        "MutationRecord",
        vec![
            ("action_id", mutation.action_id.as_str().into()),
            ("ordinal", mutation.ordinal.into()),
            ("namespace", mutation.namespace.as_str().into()),
            ("key", mutation.key.as_str().into()),
            ("before", mutation.before.clone()),
            ("after", mutation.after.clone()),
        ],
    )
}

fn consequence(consequence: &ConsequenceRecord) -> Json {
    object(
        "ConsequenceRecord",
        vec![
            ("action_id", consequence.action_id.as_str().into()),
            ("ordinal", consequence.ordinal.into()),
            ("payload", Json::Object(consequence.payload.clone())),
        ],
    )
}

fn continuity(view: &CampaignContinuityView) -> Json {
    let records = view
        .records
        .iter()
        .map(|record| {
            object(
                "CampaignContinuityRecord",
                vec![
                    ("entry_id", record.entry_id.as_str().into()),
                    ("record_id", record.record_id.as_str().into()),
                    ("kind", record.kind.as_str().into()),
                    ("status", record.status.as_str().into()),
                    ("payload", Json::Object(record.payload.clone())),
                    (
                        "basis_message_ids",
                        record.basis_message_ids.iter().map(|id| Json::from(id.as_str())).collect(),
                    ),
                    ("admitted_revision", record.admitted_revision.into()),
                    ("operation_id", optional(record.operation_id.as_deref())),
                ],
            )
        })
        .collect();
    object(
        "CampaignContinuityView",
        vec![
            ("current_scene", view.current_scene.clone().map_or(Json::Null, Json::Object)),
            ("facts", view.facts.iter().cloned().map(Json::Object).collect()),
            ("reveals", Json::Object(view.reveals.clone())),
            ("plan_progress", view.plan_progress.iter().cloned().map(Json::Object).collect()),
            ("records", Json::Array(records)),
        ],
    )
}

fn turns(turns: &[(String, TurnStatus)]) -> Json {
    Json::Object(turns.iter().map(|(id, status)| (id.clone(), Json::from(status.as_str()))).collect())
}

/// The fields every snapshot shares, then the history.
fn snapshot_json(class: &str, state: &CampaignState, history: Vec<(&str, Json)>) -> Json {
    let mut fields = vec![
        ("campaign_id", state.campaign_id.as_str().into()),
        ("display_name", state.display_name.as_str().into()),
        ("lifecycle", state.lifecycle.as_str().into()),
        ("writer", state.writer.as_str().into()),
        ("revision", state.revision.into()),
        ("package_lock", lock(&state.package_lock)),
        ("state", Json::Object(state.state.clone())),
        ("participants", state.participants.iter().map(participant).collect()),
    ];
    fields.extend(history);
    object(class, fields)
}

fn failure(error: sylvia_store::StoreError) -> Json {
    let coded = error.rejection().unwrap_or_else(|| panic!("an unexpected failure: {error}"));
    json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
}

fn outcome(read: StoreResult<Json>) -> Json {
    match read {
        Ok(value) => json!({ "ok": value }),
        Err(error) => failure(error),
    }
}

fn query(value: &Json) -> DisplayQuery {
    DisplayQuery {
        event_after: value["event_after"].as_i64(),
        actions_after_revision: value["actions_after_revision"].as_i64(),
        action_ids: value["action_ids"]
            .as_array()
            .map_or_else(Vec::new, |ids| ids.iter().map(|id| id.as_str().unwrap().to_owned()).collect()),
        include_continuity: value["include_continuity"].as_bool().unwrap_or(false),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn snapshots_match_python() {
    let record: Json = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/snapshots/snapshot_runs.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 261);
    let mut failures = Vec::new();
    let mut current: Option<(String, StoreHandle, sylvia_store::StoreThread)> = None;
    for (index, run) in runs.iter().enumerate() {
        let name = run["base"].as_str().unwrap();
        if current.as_ref().map(|(known, _, _)| known.as_str()) != Some(name) {
            if let Some((_, handle, thread)) = current.take() {
                drop(handle);
                tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
            }
            let folder =
                Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(format!("snapshots-{name}"));
            // A folder left by an earlier run must go, or its files would look like this run's.
            if let Err(error) = std::fs::remove_dir_all(&folder) {
                assert_eq!(
                    error.kind(),
                    std::io::ErrorKind::NotFound,
                    "cannot clear the old work folder {}: {error}",
                    folder.display()
                );
            }
            std::fs::create_dir_all(&folder).unwrap();
            let database = folder.join("authority.sqlite3");
            std::fs::write(&database, base(name)).unwrap();
            let (handle, thread) =
                StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&database, None))
                    .unwrap();
            current = Some((name.to_owned(), handle, thread));
        }
        let handle = &current.as_ref().unwrap().1;
        let campaign_id = run["campaign_id"].as_str().unwrap().to_owned();
        let got = match run["read"].as_str().unwrap() {
            "snapshot" => outcome(snapshots::snapshot(handle, &campaign_id).await.map(|snapshot| {
                snapshot_json(
                    "CampaignSnapshot",
                    &snapshot.state,
                    vec![
                        ("actions", snapshot.actions.iter().map(action).collect()),
                        ("events", snapshot.events.iter().map(event).collect()),
                        ("mutations", snapshot.mutations.iter().map(mutation).collect()),
                        ("consequences", snapshot.consequences.iter().map(consequence).collect()),
                        ("turns", turns(&snapshot.turns)),
                        ("continuity", continuity(&snapshot.continuity)),
                    ],
                )
            })),
            "model_context_snapshot" => outcome(
                snapshots::model_context_snapshot(handle, &campaign_id).await.map(|(snapshot, boundary)| {
                    json!([display_json(&snapshot), boundary.as_ref().map_or(Json::Null, boundary_json)])
                }),
            ),
            _ => {
                let asked = query(&run["query"]);
                outcome(
                    handle
                        .call(move |store| store.display_snapshot(&campaign_id, &asked))
                        .await
                        .map(|snapshot| display_json(&snapshot)),
                )
            }
        };
        let want = &run["outcome"];
        // As text: object equality ignores key order, and Python's order is part of the value.
        let (got_text, want_text) = (got.to_string(), want.to_string());
        if got_text != want_text {
            failures.push(format!(
                "#{index} {name} {} {}:\n  Rust   {got}\n  Python {want}",
                run["campaign_id"], run["read"]
            ));
        }
    }
    if let Some((_, handle, thread)) = current.take() {
        drop(handle);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    }
    assert!(
        failures.is_empty(),
        "{} of {} reads differ:\n{}",
        failures.len(),
        runs.len(),
        failures.join("\n")
    );
}

fn display_json(snapshot: &sylvia_domain::authority::CampaignDisplaySnapshot) -> Json {
    snapshot_json(
        "CampaignDisplaySnapshot",
        &snapshot.state,
        vec![
            ("actions", snapshot.actions.iter().map(action).collect()),
            ("events", snapshot.events.iter().map(event).collect()),
            ("turns", turns(&snapshot.turns)),
            ("continuity", continuity(&snapshot.continuity)),
        ],
    )
}

fn boundary_json(boundary: &sylvia_domain::authority::ModelDecisionBoundary) -> Json {
    let actors = boundary
        .eligible_actors
        .iter()
        .map(|actor| {
            object(
                "ModelDecisionActor",
                vec![
                    ("actor_id", actor.actor_id.as_str().into()),
                    ("actor_name", actor.actor_name.as_str().into()),
                    ("actor_state", Json::Object(actor.actor_state.clone())),
                ],
            )
        })
        .collect();
    object(
        "ModelDecisionBoundary",
        vec![
            ("owner", boundary.owner.as_str().into()),
            ("actor_id", optional(boundary.actor_id.as_deref())),
            ("actor_name", optional(boundary.actor_name.as_deref())),
            ("actor_state", Json::Object(boundary.actor_state.clone())),
            ("campaign_revision", boundary.campaign_revision.into()),
            ("active_side_id", optional(boundary.active_side_id.as_deref())),
            ("focused_actor_id", optional(boundary.focused_actor_id.as_deref())),
            ("eligible_actors", Json::Array(actors)),
            ("preparation", boundary.preparation.clone().map_or(Json::Null, Json::Object)),
        ],
    )
}
