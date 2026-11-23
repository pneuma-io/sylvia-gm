//! Mechanics that change live characters against Python (`tests/vectors/participant_mutations/
//! participant_mutation_runs.json`, written by `generate_participant_mutation_runs.py`): SRD
//! Lite's awards and confirmed level-ups on five campaigns, replayed through
//! `sylvia_app::authority::actions::{execute_action, resolve_mechanics_choice}` in order.
//!
//! The base is built here ([`installed_base`]): the fresh seed, the bundled SRD Lite archive
//! installed through the ported `install_package_archive` (every table's digest must be the one
//! Python's install left), then the campaigns' setup rows Python's owner wrote. Each receipt,
//! awaiting-choice receipt or refusal must be Python's (with the generator's direct SQL steps run
//! the same way); each table the run changes must end with Python's rows, and every other table
//! as the base has it.

mod installed_base;

use serde_json::json;
use std::path::Path;
use sylvia_app::authority::actions::{
    execute_action, resolve_mechanics_choice, ActionError, ActionExecution,
};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    ActionCommand, MechanicOutcome, MechanicsChoiceRequest, ParticipantMutation,
    ResolveMechanicsChoiceCommand, WriterRole,
};
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreHandle};

fn text(value: &Json, key: &str) -> String {
    value[key].as_str().unwrap().to_owned()
}

fn action(value: &Json) -> ActionCommand {
    ActionCommand {
        campaign_id: text(value, "campaign_id"),
        action_id: text(value, "action_id"),
        idempotency_key: text(value, "idempotency_key"),
        owner_id: text(value, "owner_id"),
        writer: WriterRole::parse(value["writer"].as_str().unwrap()).unwrap(),
        expected_revision: value["expected_revision"].as_i64().unwrap(),
        turn_id: text(value, "turn_id"),
        package_id: text(value, "package_id"),
        tool_name: text(value, "tool_name"),
        parameters: value["parameters"].as_object().unwrap().clone(),
        initiating_session_id: None,
    }
}

fn resolution(value: &Json) -> ResolveMechanicsChoiceCommand {
    ResolveMechanicsChoiceCommand {
        campaign_id: text(value, "campaign_id"),
        action_id: text(value, "action_id"),
        resolution_id: text(value, "resolution_id"),
        owner_id: text(value, "owner_id"),
        expected_revision: value["expected_revision"].as_i64().unwrap(),
        choice_id: text(value, "choice_id"),
        option_id: text(value, "option_id"),
        session_id: value["session_id"].as_str().map(str::to_owned),
        choice_sequence: value["choice_sequence"].as_i64().unwrap(),
    }
}

/// The generator's `failure`: class, code and message.
fn failure(error: ActionError) -> Json {
    match error {
        ActionError::Domain(DomainError::Rejected { error_type, code, message, .. }) => {
            json!({"error": error_type, "code": code, "message": message})
        }
        other => {
            let coded = other.into_coded();
            json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
        }
    }
}

/// The generator's `owned` step: the store's `mechanic_mutations_are_owned` for one participant
/// replacement, called directly. A failure is recorded as Python's `getattr(error, "code", None)`
/// gives it: only the authority classes carry a code.
async fn owned_check(handle: &StoreHandle, command: &Json) -> Json {
    let text = |key: &str| command[key].as_str().unwrap().to_owned();
    let (campaign_id, package_id, version, sha256) =
        (text("campaign_id"), text("package_id"), text("package_version"), text("package_sha256"));
    let outcome = MechanicOutcome {
        result: JsonMap::new(),
        mutations: Vec::new(),
        consequences: Vec::new(),
        choice: None,
        participant_mutations: vec![ParticipantMutation {
            participant_id: text("participant_id"),
            data: command["data"].as_object().unwrap().clone(),
        }],
    };
    let checked = handle
        .call(move |store| {
            store.mechanic_mutations_are_owned(&campaign_id, &package_id, &version, &sha256, &outcome)
        })
        .await;
    match checked {
        Ok(owned) => json!({"ok": owned}),
        Err(error) => {
            let coded = error.rejection().unwrap_or_else(|| panic!("an uncaught store failure: {error}"));
            let class = coded.error_type.unwrap_or("CommandRejected");
            let authority = matches!(
                class,
                "AuthorityError"
                    | "SchemaIdentityError"
                    | "OwnerConflict"
                    | "WrongOwner"
                    | "WrongWriter"
                    | "StaleRevision"
                    | "IdempotencyConflict"
                    | "CommandRejected"
                    | "MechanicsRejected"
                    | "RulesetPreconditionRejected"
            );
            json!({"error": class, "code": if authority { Json::from(coded.code) } else { Json::Null }, "message": coded.message})
        }
    }
}

fn request(choice: &MechanicsChoiceRequest) -> Json {
    let options: Vec<Json> = choice
        .options
        .iter()
        .map(|option| {
            json!({"__class__": "MechanicsChoiceOption", "option_id": option.option_id, "label": option.label})
        })
        .collect();
    json!({"__class__": "MechanicsChoiceRequest", "choice_id": choice.choice_id, "prompt": choice.prompt, "options": options})
}

/// The generator's `plain` of a receipt.
fn receipt(execution: ActionExecution) -> Json {
    match execution {
        ActionExecution::Receipt(receipt) => json!({"ok": {
            "__class__": "ActionReceipt",
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "status": receipt.status.as_str(),
            "result": Json::Object(receipt.result),
            "resulting_revision": receipt.resulting_revision,
        }}),
        ActionExecution::AwaitingChoice(receipt) => json!({"ok": {
            "__class__": "AwaitingChoiceReceipt",
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "basis_revision": receipt.basis_revision,
            "turn_id": receipt.turn_id,
            "choice": request(&receipt.choice),
            "choice_sequence": receipt.choice_sequence,
        }}),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn participant_mutations_commit_as_pythons_owner_commits_them() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/participant_mutations");
    let record: Json = serde_json::from_str(
        &std::fs::read_to_string(vectors.join("participant_mutation_runs.json")).unwrap(),
    )
    .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 25);
    let owner_id = record["owner_id"].as_str().unwrap().to_owned();
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("participant-mutations");
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
    installed_base::seed_copy(&database);
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let open = |path: std::path::PathBuf| {
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap()
    };
    let (handle, thread) = open(database.clone());
    installed_base::install(
        &handle,
        bundled.bundled_srd_archive().unwrap(),
        record["install_id"].as_str().unwrap(),
    )
    .await;
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let mismatched =
        installed_base::check_digests(&database, record["installed_table_digests"].as_object().unwrap());
    assert!(mismatched.is_empty(), "the Rust install differs from Python's:\n{}", mismatched.join("\n"));
    installed_base::write_rows(&database, record["setup_tables"].as_object().unwrap());
    let base = folder.join("base.sqlite3");
    std::fs::copy(&database, &base).unwrap();

    let (handle, thread) = open(database.clone());
    let mut failures = Vec::new();
    for (index, run) in runs.iter().enumerate() {
        let command = &run["command"];
        if run["kind"] == "sql" {
            // The generator's direct writes beside the open store, run the same way.
            let parameters: Vec<String> = command["parameters"]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_str().unwrap().into())
                .collect();
            let connection = rusqlite::Connection::open(&database).unwrap();
            connection
                .execute(command["statement"].as_str().unwrap(), rusqlite::params_from_iter(parameters))
                .unwrap();
            continue;
        }
        if run["kind"] == "owned" {
            let got = owned_check(&handle, command).await;
            let (got, want) = (got.to_string(), run["outcome"].to_string());
            if got != want {
                failures.push(format!("#{index} {}\n  Rust   {got}\n  Python {want}", run["name"]));
            }
            continue;
        }
        let outcome = match run["kind"].as_str().unwrap() {
            "act" => execute_action(&handle, &owner_id, &action(command)).await,
            "resolve" => resolve_mechanics_choice(&handle, &owner_id, &resolution(command)).await,
            kind => panic!("unknown step kind {kind}"),
        };
        let got = match outcome {
            Ok(execution) => receipt(execution),
            Err(error) => failure(error),
        };
        let (got, want) = (got.to_string(), run["outcome"].to_string());
        if got != want {
            failures.push(format!("#{index} {}\n  Rust   {got}\n  Python {want}", run["name"]));
        }
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    failures.extend(installed_base::changed_tables_differ(
        &database,
        &base,
        record["final_changed_tables"].as_object().unwrap(),
    ));
    assert!(failures.is_empty(), "{} participant steps differ:\n{}", failures.len(), failures.join("\n"));
}
