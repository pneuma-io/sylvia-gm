//! `application/desktop_update/restart.py`: one restart intent and the exact app, owner and
//! data acknowledgement. Saved work is never replayed.

use super::checkpoint::schema_identity;
use super::manifest::{bounded_json, Version};
use super::storage::{
    atomic_receipt, checked_file, directory_identity, ensure_directory, operation_id, operation_id_text,
    os_error, phase as update_phase, present, Identity,
};
use super::update_error;
use crate::util::uuid4_hex;
use std::path::{Path, PathBuf};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `RestartPhase`.
pub mod phase {
    pub const PENDING: &str = "pending";
    pub const LAUNCHING: &str = "launching";
    pub const LAUNCHED: &str = "launched";
    pub const LAUNCH_FAILED: &str = "launch_failed";
}

/// `RESTART_MOVES`, kept as data as Python's tests use it.
pub const RESTART_MOVES: [(&str, &[&str]); 4] = {
    use phase::*;
    [
        (PENDING, &[PENDING, LAUNCHING]),
        (LAUNCHING, &[PENDING, LAUNCHED, LAUNCH_FAILED]),
        (LAUNCHED, &[PENDING]),
        (LAUNCH_FAILED, &[PENDING]),
    ]
};

/// `MAX_RECOVERY_STARTS`.
pub const MAX_RECOVERY_STARTS: usize = 64;

const TICKET_KEYS: [&str; 11] = [
    "format_version",
    "operation_id",
    "attempt",
    "nonce",
    "phase",
    "version",
    "target",
    "distribution",
    "storage_compatibility",
    "data_identity",
    "app_identity",
];

/// The application, owner and data a restarted owner must be.
#[derive(Debug, Clone)]
pub struct RestartContext<'a> {
    pub nonce: &'a str,
    pub live: &'a Path,
    pub desktop_version: &'a str,
    pub owner_version: &'a str,
    pub target: &'a str,
    pub distribution: &'a str,
    pub storage_compatibility: i64,
}

fn identity_value(identity: Identity) -> Json {
    Json::Array(vec![identity.0.into(), identity.1.into()])
}

fn invalid_restart(message: &str) -> CodedError {
    update_error("invalid_restart", message)
}

/// `RestartHandshake`.
#[derive(Debug, Clone)]
pub struct RestartHandshake {
    pub root: PathBuf,
    pub identity: String,
    pub attempt: String,
    pub stage: PathBuf,
    pub recovery: bool,
    pub selection: PathBuf,
    root_identity: Identity,
    stage_identity: Identity,
}

impl RestartHandshake {
    /// `RestartHandshake(data_root, identity, attempt, recovery=)`.
    pub fn new(data_root: &Path, identity: &str, attempt: &str, recovery: bool) -> CodedResult<Self> {
        let identity = operation_id_text(identity)?;
        let attempt = operation_id_text(attempt)?;
        let stage = data_root.join(".application-updates").join(&identity).join("attempts").join(&attempt);
        let root_identity = directory_identity(data_root)?;
        let stage_identity = directory_identity(&stage)?;
        Ok(Self {
            root: data_root.to_path_buf(),
            selection: stage.join("rollback-restart.json"),
            identity,
            attempt,
            stage,
            recovery,
            root_identity,
            stage_identity,
        })
    }

    /// `exists()`: whether a restart intent (or, for recovery, its selection) was written.
    pub fn exists(&self) -> bool {
        present(&if self.recovery { self.selection.clone() } else { self.stage.join("restart.json") })
    }

    /// `_generation()`: the selected recovery start's folder.
    fn generation(&self) -> CodedResult<PathBuf> {
        let selected = bounded_json(&checked_file(&self.selection, 4096)?, super::manifest::MANIFEST_LIMIT)?;
        let valid = selected.as_object().is_some_and(|object| {
            object.len() == 2
                && object.contains_key("nonce")
                && object
                    .get("format_version")
                    .is_some_and(|value| value.is_number() && value.as_i64() == Some(1))
        });
        if !valid {
            return Err(invalid_restart("The selected recovery restart is invalid."));
        }
        let path = self.stage.join("rollback-starts").join(operation_id(&selected["nonce"])?);
        directory_identity(&path)?;
        Ok(path)
    }

    /// `ticket`: the restart intent's file.
    pub fn ticket(&self) -> CodedResult<PathBuf> {
        Ok(if self.recovery {
            self.generation()?.join("restart.json")
        } else {
            self.stage.join("restart.json")
        })
    }

    /// `receipt`: the started owner's acknowledgement.
    pub fn receipt(&self) -> CodedResult<PathBuf> {
        Ok(if self.recovery {
            self.generation()?.join("startup-confirmed.json")
        } else {
            self.stage.join("startup-confirmed.json")
        })
    }

    /// `error_receipt`: the helper's failure, written for this start.
    pub fn error_receipt(&self) -> CodedResult<PathBuf> {
        Ok(if self.recovery {
            self.generation()?.join("helper-error.json")
        } else {
            self.stage.join("helper-error.json")
        })
    }

    /// `_check()`.
    fn check(&self) -> CodedResult<()> {
        if directory_identity(&self.root)? != self.root_identity
            || directory_identity(&self.stage)? != self.stage_identity
        {
            return Err(update_error("changed_restart_root", "The intended update data location changed."));
        }
        Ok(())
    }

    /// The `rollback-starts` folder's entries, counted as `enumerate(iterdir(), 1)` does.
    fn starts(path: &Path) -> CodedResult<usize> {
        let mut count = 0;
        for entry in std::fs::read_dir(path).map_err(|error| os_error(&error))? {
            entry.map_err(|error| os_error(&error))?;
            count += 1;
        }
        Ok(count)
    }

    /// `_select_recovery(row)`: a new generation folder with its ticket, selected last.
    fn select_recovery(&self, row: &JsonMap) -> CodedResult<()> {
        self.check()?;
        let generations = self.stage.join("rollback-starts");
        ensure_directory(&generations)?;
        // Only explicit failed or ambiguous starts keep generations; ordinary checks do not.
        if Self::starts(&generations)? >= MAX_RECOVERY_STARTS {
            return Err(update_error("restart_limit", "Retained recovery attempts need repair review."));
        }
        let nonce = operation_id(&row["nonce"])?;
        let generation = generations.join(&nonce);
        std::fs::create_dir(&generation).map_err(|error| os_error(&error))?;
        directory_identity(&generation)?;
        atomic_receipt(&generation.join("restart.json"), &Json::Object(row.clone()))?;
        self.check()?;
        // Selected last: an interrupted write leaves the earlier intent selected and unchanged.
        let mut selection = JsonMap::new();
        selection.insert("format_version".into(), 1.into());
        selection.insert("nonce".into(), nonce.into());
        atomic_receipt(&self.selection, &Json::Object(selection))
    }

    /// `restart_capacity()`: whether another recovery start may be retained.
    pub fn restart_capacity(&self) -> CodedResult<bool> {
        let path = self.stage.join("rollback-starts");
        if !present(&path) {
            return Ok(true);
        }
        directory_identity(&path)?;
        Ok(Self::starts(&path)? < MAX_RECOVERY_STARTS)
    }

    /// `create(live=, version=, target=, distribution=, owner_storage_compatibility=)`: a new
    /// pending intent; returns its nonce.
    pub fn create(
        &self,
        live: &Path,
        version: &Version,
        target: &str,
        distribution: &str,
        owner_storage_compatibility: &Json,
    ) -> CodedResult<String> {
        self.check()?;
        let compatibility = owner_storage_compatibility
            .as_i64()
            .filter(|value| owner_storage_compatibility.is_number() && *value >= 1);
        if target != "windows-x86_64"
            || !matches!(distribution, "installed" | "portable")
            || compatibility.is_none()
        {
            return Err(invalid_restart("The expected owner identity is unsupported."));
        }
        if self.exists() {
            return Err(update_error("restart_exists", "An existing restart intent must be reconciled."));
        }
        let nonce = uuid4_hex();
        let mut row = JsonMap::new();
        row.insert("format_version".into(), 1.into());
        row.insert("operation_id".into(), self.identity.clone().into());
        row.insert("attempt".into(), self.attempt.clone().into());
        row.insert("nonce".into(), nonce.clone().into());
        row.insert("phase".into(), phase::PENDING.into());
        row.insert("version".into(), version.text.clone().into());
        row.insert("target".into(), target.into());
        row.insert("distribution".into(), distribution.into());
        row.insert("storage_compatibility".into(), owner_storage_compatibility.clone());
        row.insert("data_identity".into(), identity_value(self.root_identity));
        row.insert("app_identity".into(), identity_value(directory_identity(live)?));
        if self.recovery {
            self.select_recovery(&row)?;
        } else {
            atomic_receipt(&self.ticket()?, &Json::Object(row))?;
        }
        Ok(nonce)
    }

    /// `retry_recovery(expected_nonce)`: an explicit retry of a reviewed recovery start, under
    /// the operation and data locks the recovery owner holds.
    pub fn retry_recovery(&self, expected_nonce: &str) -> CodedResult<String> {
        if !self.recovery {
            return Err(invalid_restart("Only recovery has an explicit restart retry."));
        }
        let row = self.load()?;
        if Json::String(operation_id_text(expected_nonce)?) != row["nonce"] {
            return Err(update_error("stale_restart", "The reviewed recovery restart changed."));
        }
        // The earlier generation, acknowledgement and failure stay unchanged in their folder.
        let nonce = uuid4_hex();
        let mut pending = row.clone();
        pending.insert("nonce".into(), nonce.clone().into());
        pending.insert("phase".into(), phase::PENDING.into());
        self.select_recovery(&pending)?;
        Ok(nonce)
    }

    /// `_load()`: the intent, checked against this attempt and data folder.
    pub fn load(&self) -> CodedResult<JsonMap> {
        self.check()?;
        let row = bounded_json(&checked_file(&self.ticket()?, 8192)?, super::manifest::MANIFEST_LIMIT)?;
        let inconsistent = || invalid_restart("The retained restart intent is inconsistent.");
        let Json::Object(row) = row else {
            return Err(inconsistent());
        };
        let integer = |key: &str| row.get(key).filter(|value| value.is_number()).and_then(Json::as_i64);
        if row.len() != TICKET_KEYS.len()
            || !TICKET_KEYS.iter().all(|key| row.contains_key(*key))
            || integer("format_version") != Some(1)
            || row["operation_id"] != self.identity.as_str()
            || row["attempt"] != self.attempt.as_str()
            || !matches!(
                row["phase"].as_str(),
                Some(phase::PENDING | phase::LAUNCHING | phase::LAUNCHED | phase::LAUNCH_FAILED)
            )
            || row["target"] != "windows-x86_64"
            || !matches!(row["distribution"].as_str(), Some("installed" | "portable"))
            || !integer("storage_compatibility").is_some_and(|value| value >= 1)
            || row["data_identity"] != identity_value(self.root_identity)
        {
            return Err(inconsistent());
        }
        operation_id(&row["nonce"])?;
        if self.recovery {
            let generation = self.generation()?;
            if generation.file_name().map(|name| name.to_string_lossy().to_string()).as_deref()
                != row["nonce"].as_str()
            {
                return Err(invalid_restart("The recovery generation does not match its ticket."));
            }
        }
        Version::parse(&row["version"])?;
        for name in ["data_identity", "app_identity"] {
            let valid = row[name].as_array().is_some_and(|parts| {
                parts.len() == 2
                    && parts.iter().all(|part| part.is_u64() || part.as_i64().is_some_and(|value| value >= 0))
            });
            if !valid {
                return Err(invalid_restart("A retained restart location is inconsistent."));
            }
        }
        Ok(row)
    }

    /// `launch_once(launch)`: one launch, recorded before and after. A failed save after the
    /// launch leaves it ambiguous; no automatic second launch follows.
    pub fn launch_once<T>(&self, launch: impl FnOnce(&[String]) -> std::io::Result<T>) -> CodedResult<T> {
        let mut row = self.load()?;
        if row["phase"] != phase::PENDING {
            return Err(update_error(
                "restart_already_attempted",
                "This restart has already been attempted. Reconcile its result before starting another process.",
            ));
        }
        let ticket = self.ticket()?;
        row.insert("phase".into(), phase::LAUNCHING.into());
        atomic_receipt(&ticket, &Json::Object(row.clone()))?;
        let flag = if self.recovery { "--sylvia-update-recovery-start" } else { "--sylvia-update-start" };
        let arguments = [
            flag.to_string(),
            self.identity.clone(),
            self.attempt.clone(),
            row["nonce"].as_str().unwrap_or_default().to_string(),
        ];
        match launch(&arguments) {
            Ok(process) => {
                row.insert("phase".into(), phase::LAUNCHED.into());
                atomic_receipt(&ticket, &Json::Object(row))?;
                Ok(process)
            }
            Err(_) => {
                row.insert("phase".into(), phase::LAUNCH_FAILED.into());
                atomic_receipt(&ticket, &Json::Object(row))?;
                Err(update_error("restart_failed", "The updated app could not start. Recovery is retained."))
            }
        }
    }

    /// `authorize(...)`: the exact native and owner context, checked before migrations
    /// without acknowledging startup.
    pub fn authorize(&self, context: &RestartContext<'_>) -> CodedResult<JsonMap> {
        let row = self.load()?;
        let wrong =
            || update_error("wrong_restart", "The restarted app, owner or data context did not match.");
        // Python's `or` chain: the phase first, then the nonce, which is validated only then.
        if !matches!(row["phase"].as_str(), Some(phase::LAUNCHING | phase::LAUNCHED)) {
            return Err(wrong());
        }
        let nonce = operation_id_text(context.nonce)?;
        if row["nonce"] != nonce.as_str()
            || row["version"] != context.desktop_version
            || row["version"] != context.owner_version
            || row["target"] != context.target
            || row["distribution"] != context.distribution
            || row["storage_compatibility"] != context.storage_compatibility
            || identity_value(directory_identity(context.live)?) != row["app_identity"]
        {
            return Err(update_error(
                "wrong_restart",
                "The restarted app, owner or data context did not match.",
            ));
        }
        Ok(row)
    }

    /// `confirm(...)`: called once the actual app and owner have opened usable storage, never
    /// before migration. A matching earlier acknowledgement is kept as it is.
    pub fn confirm(&self, context: &RestartContext<'_>) -> CodedResult<()> {
        let row = self.authorize(context)?;
        let (schema_version, schema_sha256) = schema_identity(&self.root.join("authority.sqlite3"))?;
        let mut value = row;
        value.insert("phase".into(), update_phase::CONFIRMED.into());
        value.insert("desktop_version".into(), context.desktop_version.into());
        value.insert("owner_version".into(), context.owner_version.into());
        value.insert("schema_version".into(), schema_version.into());
        value.insert("schema_sha256".into(), schema_sha256.into());
        let receipt = self.receipt()?;
        if present(&receipt) {
            let prior = bounded_json(&checked_file(&receipt, 8192)?, super::manifest::MANIFEST_LIMIT)?;
            if prior != Json::Object(value) {
                return Err(update_error(
                    "changed_startup_receipt",
                    "An earlier startup result is preserved.",
                ));
            }
            return Ok(());
        }
        atomic_receipt(&receipt, &Json::Object(value))
    }

    /// `confirmed()`: whether a consistent acknowledgement exists for this intent.
    pub fn confirmed(&self) -> CodedResult<bool> {
        let row = self.load()?;
        let receipt_path = self.receipt()?;
        if !present(&receipt_path) {
            return Ok(false);
        }
        let receipt = bounded_json(&checked_file(&receipt_path, 8192)?, super::manifest::MANIFEST_LIMIT)?;
        let wrong = || update_error("wrong_startup_receipt", "The startup acknowledgement is inconsistent.");
        if !matches!(row["phase"].as_str(), Some(phase::LAUNCHING | phase::LAUNCHED)) {
            return Err(wrong());
        }
        let Json::Object(receipt) = receipt else {
            return Err(wrong());
        };
        let extra = ["schema_version", "schema_sha256", "desktop_version", "owner_version"];
        let keys_ok = receipt.len()
            == row.len() + extra.iter().filter(|key| !row.contains_key(**key)).count()
            && row.keys().all(|key| receipt.contains_key(key))
            && extra.iter().all(|key| receipt.contains_key(*key));
        let sha = receipt.get("schema_sha256").and_then(Json::as_str);
        if !keys_ok
            || receipt.get("phase") != Some(&Json::String(update_phase::CONFIRMED.into()))
            || receipt["desktop_version"] != row["version"]
            || receipt["owner_version"] != row["version"]
            || row.iter().any(|(key, value)| key != "phase" && receipt.get(key) != Some(value))
            || !receipt["schema_version"].is_number()
            || !receipt["schema_version"].as_i64().is_some_and(|version| version >= 0)
            || !sha.is_some_and(|sha| {
                sha.len() == 64 && sha.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
            })
        {
            return Err(wrong());
        }
        Ok(true)
    }
}
