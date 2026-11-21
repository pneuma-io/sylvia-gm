//! `application/desktop_update/recovery.py`: a deliberate rollback that holds data ownership
//! across the checkpoint and the application replacement.

use super::checkpoint::{
    acquire_update_data_lock, DataCheckpoint, RESTORE_CONFIRMATION, STORAGE_COMPATIBILITY,
};
use super::manifest::{bounded_json, MANIFEST_LIMIT};
use super::preparation::PreparedUpdate;
use super::restart::RestartHandshake;
use super::storage::{atomic_receipt, checked_file, phase, present};
use super::update_error;
use std::path::PathBuf;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `KEEP_DATA`: keep the newer data with the restored application.
pub const KEEP_DATA: &str = "Keep current data";

/// `_RECOVERABLE`: the phases of a prepared attempt that recovery may act on.
const RECOVERABLE: [&str; 10] = [
    phase::PREPARED,
    phase::CLOSING,
    phase::APPLYING,
    phase::AWAITING_RESTART,
    phase::RECOVERY_REQUIRED,
    phase::CONFIRMED,
    phase::ROLLING_BACK,
    phase::ROLLBACK_READY,
    phase::ROLLBACK_AWAITING_RESTART,
    phase::ROLLED_BACK,
];

/// `RecoveryOperation`: uses the retained old helper's storage contract and an already
/// authenticated preparation.
#[derive(Debug, Clone)]
pub struct RecoveryOperation {
    pub prepared: PreparedUpdate,
    pub checkpoint: DataCheckpoint,
    pub compatibility: i64,
    pub choice: PathBuf,
    pub handshake: RestartHandshake,
}

/// Run `work` holding the lock taken by `lock`, releasing it on every path.
fn holding<T>(
    lock: CodedResult<sylvia_store::owner_lock::OwnerLock>,
    work: impl FnOnce() -> CodedResult<T>,
) -> CodedResult<T> {
    let mut lock = lock?;
    let outcome = work();
    lock.close();
    outcome
}

impl RecoveryOperation {
    /// `RecoveryOperation(prepared)`.
    pub fn new(prepared: PreparedUpdate) -> CodedResult<Self> {
        let checkpoint = DataCheckpoint::new(&prepared.root.join("authority.sqlite3"), &prepared.stage)?;
        let compatibility =
            prepared.checkpoint_binding()?["storage_compatibility"].as_i64().expect("an integer contract");
        let handshake = RestartHandshake::new(&prepared.root, &prepared.identity, &prepared.attempt, true)?;
        Ok(Self {
            choice: prepared.stage.join("recovery-choice.json"),
            prepared,
            checkpoint,
            compatibility,
            handshake,
        })
    }

    /// `_phase()`: the selected attempt's recoverable phase.
    pub fn phase(&mut self) -> CodedResult<String> {
        self.prepared.require_selected(Some(self.compatibility))?;
        let row = self.prepared.store.load()?;
        match row.filter(|row| RECOVERABLE.contains(&row.phase.as_str())) {
            Some(row) => Ok(row.phase),
            None => {
                Err(update_error("invalid_phase", "This update does not have a recoverable preparation."))
            }
        }
    }

    /// `_started_compatibility()`: the contract of the owner the update started, from its
    /// restart intent, or the checkpoint's when none was written.
    fn started_compatibility(&self) -> CodedResult<i64> {
        let original = RestartHandshake::new(
            &self.prepared.root,
            &self.prepared.identity,
            &self.prepared.attempt,
            false,
        )?;
        if present(&original.ticket()?) {
            let row = original.load()?;
            if row["version"] != self.prepared.manifest.version.text.as_str()
                || row["target"] != self.prepared.target.as_str()
                || row["distribution"] != self.prepared.distribution.as_str()
            {
                return Err(update_error("wrong_restart", "The retained new-owner identity changed."));
            }
            return Ok(row["storage_compatibility"].as_i64().expect("an integer contract"));
        }
        // Ordinary starts are fenced throughout apply, before a restart intent exists.
        Ok(self.checkpoint.load(self.compatibility)?["storage_compatibility"]
            .as_i64()
            .expect("an integer contract"))
    }

    /// The choice receipt a restore or keep decision records.
    fn choice_receipt(&self, choice: &str, checkpoint: &JsonMap, started: i64) -> Json {
        let mut expected = JsonMap::new();
        expected.insert("format_version".into(), 1.into());
        expected.insert("operation_id".into(), self.prepared.identity.clone().into());
        expected.insert("attempt".into(), self.prepared.attempt.clone().into());
        expected.insert("choice".into(), choice.into());
        expected.insert("checkpoint_version".into(), checkpoint["app_version"].clone());
        expected.insert("started_storage_compatibility".into(), started.into());
        Json::Object(expected)
    }

    /// `inspect()`: no migration or launch; a live owner or helper prevents a misleading choice.
    pub fn inspect(&mut self) -> CodedResult<JsonMap> {
        let operation_lock = acquire_update_data_lock(&self.prepared.stage.join("helper-runtime"));
        holding(operation_lock, || {
            let phase = self.phase()?;
            let data_lock = acquire_update_data_lock(&self.checkpoint.database);
            holding(data_lock, || self.inspect_locked(&phase))
        })
    }

    fn inspect_locked(&mut self, phase: &str) -> CodedResult<JsonMap> {
        let mut restart_nonce = Json::Null;
        let mut started = self.started_compatibility()?;
        if phase == phase::ROLLBACK_AWAITING_RESTART
            || (phase == phase::ROLLBACK_READY && self.handshake.exists())
        {
            restart_nonce = self.handshake.load()?["nonce"].clone();
            // A retry opens restored data; it never restores again.
            started = self.compatibility;
        }
        let compatible = if self.checkpoint.restore_pending()? {
            let checkpoint = self.checkpoint.load(self.compatibility)?;
            let expected = self.choice_receipt(RESTORE_CONFIRMATION, &checkpoint, started);
            if phase != phase::ROLLING_BACK
                || bounded_json(&checked_file(&self.choice, 4096)?, MANIFEST_LIMIT)? != expected
            {
                return Err(update_error("invalid_checkpoint", "The interrupted restore choice changed."));
            }
            // Retention can leave the live database absent, or the database and settings partly
            // restored. Only the original explicit restore may resume; keeping this is unsafe.
            false
        } else {
            match self.checkpoint.require_compatible(started, self.compatibility) {
                Ok(()) => true,
                Err(error) if error.code == "restore_choice_required" => false,
                Err(error) => return Err(error),
            }
        };
        let mut result = JsonMap::new();
        result.insert("operation_id".into(), self.prepared.identity.clone().into());
        result.insert("phase".into(), phase.into());
        result.insert("previous_version".into(), self.prepared.current.text.clone().into());
        result.insert("can_keep_data".into(), compatible.into());
        result.insert("restart_nonce".into(), restart_nonce);
        result.insert("restart_capacity".into(), self.handshake.restart_capacity()?.into());
        result.insert(
            "restore_warning".into(),
            "Restoring pre-update data loses progress since the checkpoint. The newer data will be retained locally for recovery."
                .into(),
        );
        Ok(result)
    }

    /// `rollback(choice, processes_exited)`: the helper supplies captured process handles,
    /// never a renderer's claim that processes exited.
    pub fn rollback(
        &mut self,
        choice: &str,
        processes_exited: &dyn Fn() -> CodedResult<bool>,
    ) -> CodedResult<()> {
        if choice != KEEP_DATA && choice != RESTORE_CONFIRMATION {
            return Err(update_error("restore_choice_required", "Choose how to preserve or restore data."));
        }
        let operation_lock = acquire_update_data_lock(&self.prepared.stage.join("helper-runtime"));
        holding(operation_lock, || self.rollback_locked(choice, processes_exited))
    }

    /// `_rollback_locked(choice, processes_exited)`, under the operation lock.
    pub fn rollback_locked(
        &mut self,
        choice: &str,
        processes_exited: &dyn Fn() -> CodedResult<bool>,
    ) -> CodedResult<()> {
        if choice != KEEP_DATA && choice != RESTORE_CONFIRMATION {
            return Err(update_error("restore_choice_required", "Choose how to preserve or restore data."));
        }
        if self.compatibility != STORAGE_COMPATIBILITY {
            return Err(update_error(
                "wrong_recovery_helper",
                "Use the retained helper matching this checkpoint.",
            ));
        }
        let phase = self.phase()?;
        if [phase::ROLLED_BACK, phase::ROLLBACK_READY, phase::ROLLBACK_AWAITING_RESTART]
            .contains(&phase.as_str())
        {
            // An acknowledged or pending restart must not roll back later player progress.
            return self.prepared.replacement()?.verify_rollback();
        }
        if !processes_exited()? {
            return Err(update_error("application_busy", "Close the owned application before recovery."));
        }
        let data_lock = acquire_update_data_lock(&self.checkpoint.database);
        holding(data_lock, || {
            self.phase()?;
            let checkpoint = self.checkpoint.load(STORAGE_COMPATIBILITY)?;
            let started = self.started_compatibility()?;
            let expected = self.choice_receipt(choice, &checkpoint, started);
            if present(&self.choice) {
                if bounded_json(&checked_file(&self.choice, 4096)?, MANIFEST_LIMIT)? != expected {
                    return Err(update_error(
                        "changed_recovery_choice",
                        "The interrupted recovery choice is preserved.",
                    ));
                }
            } else {
                if choice == KEEP_DATA {
                    self.checkpoint.require_compatible(started, STORAGE_COMPATIBILITY)?;
                }
                atomic_receipt(&self.choice, &expected)?;
            }
            let version = self.prepared.manifest.version.text.clone();
            self.prepared.store.save(&self.prepared.identity, phase::ROLLING_BACK, Some(&version), None)?;
            if choice == RESTORE_CONFIRMATION {
                self.checkpoint.restore_locked(processes_exited)?;
            }
            let compatibility = if choice == RESTORE_CONFIRMATION {
                checkpoint["storage_compatibility"].as_i64().expect("an integer contract")
            } else {
                started
            };
            let checkpoint = self.checkpoint.clone();
            self.prepared.replacement()?.rollback(processes_exited, &|| {
                checkpoint.require_compatible(compatibility, STORAGE_COMPATIBILITY)
            })?;
            self.prepared.replacement()?.verify_rollback()?;
            self.prepared.store.save(&self.prepared.identity, phase::ROLLBACK_READY, Some(&version), None)
        })
    }

    /// `_owner_compatibility(owner_identity)`: the restored owner must be the checkpoint's.
    fn owner_compatibility(&self, owner_identity: &Json) -> CodedResult<i64> {
        let checkpoint = self.checkpoint.load(STORAGE_COMPATIBILITY)?;
        let mut expected = JsonMap::new();
        expected.insert("format_version".into(), 1.into());
        expected.insert("product".into(), "Sylvia GM".into());
        expected.insert("component".into(), "owner".into());
        expected.insert("version".into(), self.prepared.current.text.clone().into());
        expected.insert("target".into(), self.prepared.target.clone().into());
        expected.insert("storage_compatibility".into(), checkpoint["storage_compatibility"].clone());
        let integers = owner_identity.get("format_version").is_some_and(Json::is_i64)
            && owner_identity.get("storage_compatibility").is_some_and(Json::is_i64);
        if *owner_identity != Json::Object(expected) || !integers {
            return Err(update_error("wrong_owner_identity", "The restored owner identity is inconsistent."));
        }
        Ok(checkpoint["storage_compatibility"].as_i64().expect("an integer contract"))
    }

    /// `create_restart(owner_identity)`: probe only the restored fixed sidecar, then one
    /// separate intent.
    pub fn create_restart(&mut self, owner_identity: &Json) -> CodedResult<RestartHandshake> {
        if self.phase()? != phase::ROLLBACK_READY {
            return Err(update_error("invalid_phase", "Finish application recovery before restarting."));
        }
        self.prepared.replacement()?.verify_rollback()?;
        let compatibility = self.owner_compatibility(owner_identity)?;
        if self.handshake.exists() {
            // A selection can survive a failed save. Its retained error must never be reused by
            // a later launch, even while this intent still says pending.
            return Err(update_error("recovery_required", "Review the earlier recovery restart first."));
        }
        let prepared = &self.prepared;
        self.handshake.create(
            &prepared.live,
            &prepared.current,
            &prepared.target,
            &prepared.distribution,
            &Json::from(compatibility),
        )?;
        let version = prepared.manifest.version.text.clone();
        prepared.store.save(&prepared.identity, phase::ROLLBACK_AWAITING_RESTART, Some(&version), None)?;
        Ok(self.handshake.clone())
    }

    /// `retry_restart(expected_nonce, owner_identity, processes_exited)`: an explicit consent
    /// fences the earlier start under the lock taken before migrations.
    pub fn retry_restart(
        &mut self,
        expected_nonce: &str,
        owner_identity: &Json,
        processes_exited: &dyn Fn() -> CodedResult<bool>,
    ) -> CodedResult<RestartHandshake> {
        if !processes_exited()? {
            return Err(update_error("application_busy", "Close the owned application before retrying."));
        }
        let lock = acquire_update_data_lock(&self.checkpoint.database);
        holding(lock, || {
            if ![phase::ROLLBACK_READY, phase::ROLLBACK_AWAITING_RESTART].contains(&self.phase()?.as_str()) {
                return Err(update_error("stale_restart", "The reviewed recovery is no longer pending."));
            }
            self.prepared.replacement()?.verify_rollback()?;
            let compatibility = self.owner_compatibility(owner_identity)?;
            self.checkpoint.require_compatible(compatibility, STORAGE_COMPATIBILITY)?;
            if !processes_exited()? {
                return Err(update_error("application_busy", "An owned process is still active."));
            }
            self.handshake.retry_recovery(expected_nonce)?;
            let version = self.prepared.manifest.version.text.clone();
            self.prepared.store.save(
                &self.prepared.identity,
                phase::ROLLBACK_AWAITING_RESTART,
                Some(&version),
                None,
            )
        })?;
        Ok(self.handshake.clone())
    }

    /// `confirm()`: the restored app acknowledged its data.
    pub fn confirm(&mut self) -> CodedResult<()> {
        if ![phase::ROLLBACK_AWAITING_RESTART, phase::ROLLED_BACK].contains(&self.phase()?.as_str()) {
            return Err(update_error("invalid_phase", "Recovery is no longer awaiting confirmation."));
        }
        self.prepared.replacement()?.verify_rollback()?;
        if !self.handshake.confirmed()? {
            return Err(update_error("startup_unconfirmed", "The restored app has not confirmed its data."));
        }
        let version = self.prepared.manifest.version.text.clone();
        self.prepared.store.save(&self.prepared.identity, phase::ROLLED_BACK, Some(&version), None)
    }
}

/// The error a recovery reply reports for Python's `OwnerConflict` (another owner holds the data).
pub fn owner_conflict(error: &CodedError) -> bool {
    error.error_type == Some("OwnerConflict")
}
