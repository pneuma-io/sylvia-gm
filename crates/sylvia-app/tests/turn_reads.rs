//! The Game Master round's further reads and the private provider records against Python
//! (`tests/vectors/turn_reads/turn_read_runs.json`, written by `generate_turn_read_runs.py`):
//! `current_package_lock` and `campaign_preparation_boundary` for every campaign of five
//! Python-made profiles, `campaign_narrative_options` for every session, and a scripted run of
//! `sqlite_reasoning`'s writes and reads on the session-page profile. Each outcome must be
//! Python's, compared as text so the order counts.

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::turn_reads;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{ModelDecisionBoundary, PackageLock};
use sylvia_domain::package_library::PackageLockRecord;
use sylvia_store::reasoning::{self, PrivateRecord};
use sylvia_store::{AuthorityStore, StoreHandle, StoreResult};

fn base(name: &str) -> Vec<u8> {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let path = match name {
        "cast" => manifest.join("../sylvia-store/tests/vectors/stored_authority/cast.sqlite3.gz"),
        "riichi_started" => manifest.join("tests/vectors/snapshots/riichi_started.sqlite3.gz"),
        "campaigns" => manifest.join("../sylvia-store/tests/vectors/campaigns/seed.sqlite3.gz"),
        "session_pages" => manifest.join("../sylvia-store/tests/vectors/session_pages/seed.sqlite3.gz"),
        // The generator's own profile: a DM campaign with acts, objectives, secrets and reveals.
        "narrative" => manifest.join("tests/vectors/turn_reads/narrative.sqlite3.gz"),
        other => panic!("unknown base {other}"),
    };
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

fn lock_record(record: &PackageLockRecord) -> Json {
    object(
        "PackageLockRecord",
        vec![
            ("lock_id", record.lock_id.as_str().into()),
            ("campaign_id", record.campaign_id.as_str().into()),
            ("closure_sha256", record.closure_sha256.as_str().into()),
            ("package_lock", lock(&record.package_lock)),
            ("availability", record.availability.as_str().into()),
            ("predecessor_lock_id", optional(record.predecessor_lock_id.as_deref())),
            ("created_revision", record.created_revision.into()),
        ],
    )
}

fn boundary_json(boundary: &ModelDecisionBoundary) -> Json {
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

fn outcome(read: StoreResult<Json>) -> Json {
    match read {
        Ok(value) => json!({ "ok": value }),
        Err(error) => {
            let coded = error.rejection().unwrap_or_else(|| panic!("an unexpected failure: {error}"));
            json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
        }
    }
}

/// The generator's own work folder for one base, cleared loudly.
fn open(name: &str) -> (StoreHandle, sylvia_store::StoreThread) {
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(format!("turn-reads-{name}"));
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
    StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&database, None)).unwrap()
}

fn text(step: &Json, key: &str) -> String {
    step[key].as_str().unwrap().to_owned()
}

/// One write in its own transaction, committed, or rolled back on its failure.
fn write(store: &AuthorityStore, step: &Json) -> StoreResult<Json> {
    let connection = store.connect()?;
    connection.execute_batch("BEGIN")?;
    let written = if step["op"] == "append" {
        let record = &step["record"];
        let field = |key: &str| record[key].as_str().unwrap();
        reasoning::append_record(
            &connection,
            &PrivateRecord {
                session_id: field("session_id"),
                attempt_id: field("attempt_id"),
                response_id: field("response_id"),
                turn_id: field("turn_id"),
                basis_message_id: field("basis_message_id"),
                source_order: record["source_order"].as_i64().unwrap(),
                provider_id: field("provider_id"),
                model_id: field("model_id"),
                kind: field("kind"),
                payload: field("payload"),
            },
        )
    } else {
        reasoning::finish_attempt(
            &connection,
            &text(step, "session_id"),
            &text(step, "attempt_id"),
            step["completed"].as_bool().unwrap(),
        )
    };
    match written {
        Ok(()) => {
            connection.execute_batch("COMMIT")?;
            Ok(Json::Null)
        }
        Err(error) => {
            connection.execute_batch("ROLLBACK")?;
            Err(error)
        }
    }
}

async fn private_step(handle: &StoreHandle, step: &Json) -> Json {
    let op = step["op"].as_str().unwrap().to_owned();
    match op.as_str() {
        "append" | "finish" | "inspection_index" | "read_thinking" => {
            let step = step.clone();
            outcome(
                handle
                    .call(move |store| match op.as_str() {
                        "append" | "finish" => write(store, &step),
                        "inspection_index" => {
                            let connection = store.connect_read_only()?;
                            let index = reasoning::inspection_index(&connection, &text(&step, "session_id"))?;
                            Ok(Json::Array(index.into_iter().map(Json::Object).collect()))
                        }
                        _ => {
                            let connection = store.connect_read_only()?;
                            reasoning::read_thinking(
                                &connection,
                                &text(&step, "session_id"),
                                &text(&step, "attempt_id"),
                                &text(&step, "response_id"),
                            )
                            .map(|thinking| thinking.map_or(Json::Null, Json::from))
                        }
                    })
                    .await,
            )
        }
        "provider_continuation" => outcome(
            turn_reads::provider_continuation(
                handle,
                &text(step, "session_id"),
                &text(step, "response_id"),
                &text(step, "provider_id"),
                &text(step, "model_id"),
            )
            .await
            .map(|value| value.unwrap_or(Json::Null)),
        ),
        _ => outcome(
            turn_reads::actor_context(
                handle,
                &text(step, "session_id"),
                &text(step, "context_id"),
                &text(step, "provider_id"),
                &text(step, "model_id"),
            )
            .await
            .map(|value| value.map_or(Json::Null, Json::Object)),
        ),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn turn_reads_match_python() {
    let record: Json = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/turn_reads/turn_read_runs.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let runs = record["runs"].as_array().unwrap();
    let steps = record["private_records"].as_array().unwrap();
    assert_eq!((runs.len(), steps.len()), (68, 39));
    let mut failures = Vec::new();
    for name in ["cast", "riichi_started", "campaigns", "session_pages", "narrative"] {
        let (handle, thread) = open(name);
        for (index, run) in runs.iter().enumerate().filter(|(_, run)| run["base"] == name) {
            let read = run["read"].as_str().unwrap();
            let got = match read {
                "current_package_lock" => outcome(
                    turn_reads::current_package_lock(&handle, run["campaign_id"].as_str().unwrap())
                        .await
                        .map(|record| lock_record(&record)),
                ),
                "model_decision_boundary" => outcome(
                    turn_reads::model_decision_boundary(&handle, run["campaign_id"].as_str().unwrap())
                        .await
                        .map(|boundary| boundary.as_ref().map_or(Json::Null, boundary_json)),
                ),
                "campaign_preparation_boundary" => outcome(
                    turn_reads::campaign_preparation_boundary(&handle, run["campaign_id"].as_str().unwrap())
                        .await
                        .map(|boundary| boundary.as_ref().map_or(Json::Null, boundary_json)),
                ),
                _ => outcome(
                    turn_reads::campaign_narrative_options(&handle, run["session_id"].as_str().unwrap())
                        .await
                        .map(Json::Object),
                ),
            };
            let (got, want) = (got.to_string(), run["outcome"].to_string());
            if got != want {
                failures.push(format!("#{index} {name} {read}\n  Rust   {got}\n  Python {want}"));
            }
        }
        if name == "session_pages" {
            for (index, step) in steps.iter().enumerate() {
                let (got, want) =
                    (private_step(&handle, step).await.to_string(), step["outcome"].to_string());
                if got != want {
                    failures
                        .push(format!("private #{index} {}\n  Rust   {got}\n  Python {want}", step["op"]));
                }
            }
        }
        drop(handle);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    }
    assert!(failures.is_empty(), "{} reads differ:\n{}", failures.len(), failures.join("\n"));
}
