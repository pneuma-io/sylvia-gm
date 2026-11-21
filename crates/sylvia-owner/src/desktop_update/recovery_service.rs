//! `application/desktop_update/recovery_service.py`: the recovery-only private pipe. It opens
//! no authority, runs no migration, reads no campaign and admits no provider work.
//!
//! As in 0.4.1, packaged recovery stays switched off (`_RECOVERY_QUALIFIED = False`): the state
//! is offered, a handoff is refused.

use super::checkpoint::RESTORE_CONFIRMATION;
use super::launch::Installation;
use super::manifest::{bounded_json, KeyRing, Version, APP_VERSION, MANIFEST_LIMIT};
use super::preparation::PreparedUpdate;
use super::recovery::{owner_conflict, RecoveryOperation, KEEP_DATA};
use super::storage::{checked_file, operation_id, UpdateStore};
use super::update_error;
use std::path::Path;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `_RECOVERY_QUALIFIED`: separate from public trust; enabled only after actual packaged
/// recovery qualification.
pub const RECOVERY_QUALIFIED: bool = false;

/// `_CHOICES`.
fn choice_text(choice: &str) -> Option<&'static str> {
    match choice {
        "keep" => Some(KEEP_DATA),
        "restore" => Some(RESTORE_CONFIRMATION),
        _ => None,
    }
}

/// `RecoveryService`.
#[derive(Debug, Clone)]
pub struct RecoveryService {
    pub installation: Installation,
    pub current: Version,
    pub keyring: KeyRing,
    pub native_version: String,
}

impl RecoveryService {
    /// `_prepared()`: the selected attempt of the retained update, for this app pair.
    fn prepared(&self) -> CodedResult<PreparedUpdate> {
        let native = &self.installation;
        native.check(&native.target, &native.distribution)?;
        let root = native.data.join(".application-updates");
        let Some(row) = UpdateStore::new(&root)?.load()? else {
            return Err(update_error("recovery_unavailable", "No retained update is selected."));
        };
        let identity = operation_id(&Json::String(row.operation_id))?;
        let base = root.join(&identity);
        let selected =
            bounded_json(&checked_file(&base.join("selected-attempt.json"), 4096)?, MANIFEST_LIMIT)?;
        let valid = selected.as_object().is_some_and(|object| {
            object.len() == 2
                && object.contains_key("attempt")
                && object
                    .get("format_version")
                    .is_some_and(|value| value.is_number() && value.as_i64() == Some(1))
        });
        if !valid {
            return Err(update_error("recovery_unavailable", "The retained attempt needs manual repair."));
        }
        let attempt = operation_id(&selected["attempt"])?;
        let binding = bounded_json(
            &checked_file(&base.join("attempts").join(&attempt).join("prepared-update.json"), 8192)?,
            MANIFEST_LIMIT,
        )?;
        let Some(binding) = binding.as_object() else {
            return Err(update_error("recovery_unavailable", "The retained preparation is invalid."));
        };
        let current = Version::parse(binding.get("current_version").unwrap_or(&Json::Null))?;
        let prepared = PreparedUpdate::new(
            &native.data,
            &native.live,
            &identity,
            &attempt,
            &current,
            &native.target,
            &native.distribution,
            &self.keyring,
        )?;
        let versions = [prepared.current.text.as_str(), prepared.manifest.version.text.as_str()];
        if !versions.contains(&self.current.text.as_str())
            || !versions.contains(&self.native_version.as_str())
        {
            return Err(update_error("wrong_recovery_app", "Use the application matching this update."));
        }
        RecoveryOperation::new(prepared.clone())?.phase()?;
        Ok(prepared)
    }

    /// `handle(action, params)`: `state` or `handoff`.
    pub fn handle(&self, action: &str, params: &JsonMap) -> CodedResult<JsonMap> {
        let required: &[&str] =
            if action == "state" { &["host"] } else { &["host", "operation_id", "choice", "restart_nonce"] };
        let mut host = JsonMap::new();
        host.insert("version".into(), self.native_version.clone().into());
        host.insert("target".into(), self.installation.target.clone().into());
        host.insert("distribution".into(), self.installation.distribution.clone().into());
        if !matches!(action, "state" | "handoff")
            || params.len() != required.len()
            || !required.iter().all(|key| params.contains_key(*key))
            || params["host"] != Json::Object(host)
        {
            return Err(update_error("invalid_update_request", "The native recovery context changed."));
        }
        let mut prepared = self.prepared()?;
        let mut recovery = RecoveryOperation::new(prepared.clone())?;
        let mut state = recovery.inspect()?;
        if action == "state" {
            let capacity = state["restart_capacity"] == true;
            let mut enabled = RECOVERY_QUALIFIED && capacity;
            let mut message = "Choose a data recovery option before restarting.";
            if !state["restart_nonce"].is_null() {
                message = "Review a retry of the restored application. Current data will be kept.";
                enabled = enabled && state["can_keep_data"] == true;
            }
            if !capacity {
                message = "Retained recovery attempts need repair review. Keep saved data and the recovery files intact.";
            } else if !RECOVERY_QUALIFIED {
                message = "Automatic recovery is unavailable in this build. Recovery files and saved data are retained. Reinstall the matching official application to repair it.";
            }
            state.insert("can_recover".into(), enabled.into());
            state.insert("message".into(), message.into());
            return Ok(state);
        }
        if !RECOVERY_QUALIFIED {
            return Err(update_error(
                "recovery_unavailable",
                "Packaged recovery is not enabled in this build.",
            ));
        }
        let choice = params["choice"].as_str().filter(|choice| choice_text(choice).is_some());
        let Some(choice) = choice.filter(|_| {
            operation_id(&params["operation_id"]).ok().as_deref() == Some(prepared.identity.as_str())
        }) else {
            operation_id(&params["operation_id"])?;
            return Err(update_error("invalid_update_request", "The recovery selection is invalid."));
        };
        let restart_nonce = &params["restart_nonce"];
        if !restart_nonce.is_null() {
            operation_id(restart_nonce)?;
        }
        if *restart_nonce != state["restart_nonce"]
            || state["restart_capacity"] != true
            || (!restart_nonce.is_null() && choice != "keep")
        {
            return Err(update_error(
                "stale_restart",
                "Review the current recovery restart before retrying.",
            ));
        }
        if choice == "keep" && state["can_keep_data"] != true {
            return Err(update_error("restore_choice_required", "Choose an explicit checkpoint restore."));
        }
        let (_, helper) = prepared.replacement()?.helper_payload()?;
        let mut helper_row = JsonMap::new();
        helper_row.insert("size".into(), helper.size.into());
        helper_row.insert("sha256".into(), helper.sha256.into());
        let mut result = JsonMap::new();
        result.insert("operation_id".into(), prepared.identity.clone().into());
        result.insert("attempt".into(), prepared.attempt.clone().into());
        result.insert("helper".into(), Json::Object(helper_row));
        result.insert("restart_nonce".into(), restart_nonce.clone());
        Ok(result)
    }
}

/// One line the recovery loop read, or why reading failed.
#[derive(Debug)]
pub enum RecoveryLine {
    /// A complete line, without its bounds checked.
    Line(String),
    /// Input ended.
    End,
    /// The stream could not be read as strict UTF-8 text.
    Broken,
}

/// `_read(stream)`: one request of at most 64 KiB, newline-terminated, a JSON object. `None`
/// at the end of input; an error ends the recovery owner, as Python's exception does.
fn read_request(line: RecoveryLine) -> CodedResult<Option<JsonMap>> {
    let raw = match line {
        RecoveryLine::End => return Ok(None),
        RecoveryLine::Broken => {
            return Err(CodedError::new("internal_error", "Recovery input could not be read"))
        }
        RecoveryLine::Line(raw) => raw,
    };
    let bound = || update_error("invalid_update_request", "The recovery request exceeds its bound.");
    if !raw.ends_with('\n') || raw.len() > 65536 {
        return Err(bound());
    }
    match bounded_json(raw.as_bytes(), 65536)? {
        Json::Object(row) => Ok(Some(row)),
        _ => Err(update_error("invalid_update_request", "The recovery request is invalid.")),
    }
}

/// `_write(stream, row)`: compact JSON, ASCII-only, one line.
pub fn encode_line(row: &Json) -> String {
    let mut line = pyjson::dumps(row, Dump::WIRE);
    line.push('\n');
    line
}

/// What `run_recovery` decided about its launch.
pub struct RecoveryLaunch {
    pub service: RecoveryService,
    pub ready: Json,
}

/// `run_recovery(launch, ...)`, up to the ready message: the same native launch challenge
/// authenticates a server that cannot open player data. An error ends the owner without a
/// reply, as Python's raised exception does.
pub fn recovery_launch(launch: &JsonMap) -> CodedResult<RecoveryLaunch> {
    let incomplete = || update_error("invalid_update_request", "The native recovery launch is incomplete.");
    let (Some(Json::String(raw_database)), Some(Json::String(secret))) =
        (launch.get("database_path"), launch.get("secret_hex"))
    else {
        return Err(incomplete());
    };
    if launch.get("type") != Some(&Json::String("launch".into())) {
        return Err(incomplete());
    }
    let database = Path::new(raw_database);
    if !database.is_absolute() || database.file_name().map(|name| name != "authority.sqlite3").unwrap_or(true)
    {
        return Err(update_error("invalid_update_request", "The selected data location is invalid."));
    }
    let context = launch.get("desktop_update_context");
    let installation = Installation::for_recovery(context, database.parent().unwrap_or(Path::new("")))?;
    let Some(installation) = installation else {
        return Err(update_error(
            "invalid_launch_context",
            "Recovery needs the original native copy context.",
        ));
    };
    let response = sylvia_store::launch::derive_owner_response(secret)
        .map_err(|_| CodedError::new("internal_error", "The recovery launch secret is invalid"))?;
    let native_version = context
        .and_then(|context| context.get("version"))
        .and_then(Json::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| APP_VERSION.to_string());
    let service = RecoveryService {
        installation,
        current: Version::parse_text(APP_VERSION).expect("the build version is SemVer"),
        keyring: KeyRing::new(super::service::public_keys()),
        native_version,
    };
    let mut ready = JsonMap::new();
    ready.insert("ok".into(), true.into());
    ready.insert("type".into(), "ready".into());
    ready.insert("response_hex".into(), response.into());
    ready.insert("recovery_only".into(), true.into());
    Ok(RecoveryLaunch { service, ready: Json::Object(ready) })
}

/// What one recovery line asks the loop to do.
pub enum RecoveryStep {
    /// Write this reply and read on.
    Reply(Json),
    /// Write this reply and end with exit code 0 (`shutdown`).
    Closed(Json),
    /// Input ended: exit code 0, no reply.
    End,
    /// An exception escaped: end without a reply (exit code 2).
    Failed,
}

/// One turn of `run_recovery`'s loop.
pub fn recovery_step(service: &RecoveryService, line: RecoveryLine) -> RecoveryStep {
    let row = match read_request(line) {
        Ok(Some(row)) => row,
        Ok(None) => return RecoveryStep::End,
        Err(_) => return RecoveryStep::Failed,
    };
    let identity = row.get("id").cloned().unwrap_or(Json::Null);
    let reply = |result: Result<JsonMap, CodedError>| -> Option<Json> {
        let mut reply = JsonMap::new();
        reply.insert("id".into(), identity.clone());
        match result {
            Ok(result) => {
                reply.insert("ok".into(), true.into());
                reply.insert("result".into(), Json::Object(result));
            }
            Err(error) if super::storage::is_update_error(&error) || owner_conflict(&error) => {
                let (code, message) = if owner_conflict(&error) {
                    (
                        "application_busy".to_string(),
                        "Close the other owned app or wait for its helper.".to_string(),
                    )
                } else {
                    (error.code, error.message)
                };
                let mut error = JsonMap::new();
                error.insert("code".into(), code.into());
                error.insert("message".into(), message.into());
                reply.insert("ok".into(), false.into());
                reply.insert("error".into(), Json::Object(error));
            }
            Err(_) => return None,
        }
        Some(Json::Object(reply))
    };
    let valid = identity.as_str().is_some_and(|id| (1..=128).contains(&id.chars().count()))
        && row.len() == 3
        && ["id", "method", "params"].iter().all(|key| row.contains_key(*key))
        && row["params"].is_object();
    let outcome = (|| -> Result<Result<JsonMap, CodedError>, ()> {
        if !valid {
            return Ok(Err(update_error("invalid_update_request", "The recovery request is invalid.")));
        }
        let params = row["params"].as_object().expect("checked above");
        if row["method"] == "shutdown" && params.is_empty() {
            return Err(());
        }
        let action = match row["method"].as_str() {
            Some("desktop_update_recovery_state") => "state",
            Some("desktop_update_recovery_handoff") => "handoff",
            _ => {
                return Ok(Err(update_error(
                    "recovery_only",
                    "Only update recovery is available in this window.",
                )))
            }
        };
        Ok(service.handle(action, params))
    })();
    match outcome {
        Err(()) => {
            let mut result = JsonMap::new();
            result.insert("status".into(), "closed".into());
            let mut reply = JsonMap::new();
            reply.insert("id".into(), identity.clone());
            reply.insert("ok".into(), true.into());
            reply.insert("result".into(), Json::Object(result));
            RecoveryStep::Closed(Json::Object(reply))
        }
        Ok(result) => match reply(result) {
            Some(reply) => RecoveryStep::Reply(reply),
            None => RecoveryStep::Failed,
        },
    }
}
