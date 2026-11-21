//! The SQL the Python store executes and hashes, byte for byte.
//!
//! Every file under `crates/sylvia-store/sql` is written by `sql/generate_sql.py` from the
//! frozen Python constants named beside each item here. Never edit them by hand: the stored
//! `sqlite_master` text and every `sql_sha256` in `schema_migrations` come from these bytes.

/// `sqlite_authority._MIGRATION_1`.
pub const MIGRATION_1: &str = include_str!("../sql/m01_f1_neutral_authority.sql");
/// `sqlite_authority._MIGRATION_2`.
pub const MIGRATION_2: &str = include_str!("../sql/m02_f2_provisional_event_protocol.sql");
/// `sqlite_packages.MIGRATION_3_SQL`.
pub const MIGRATION_3: &str = include_str!("../sql/m03_f3_sqlite_package_library.sql");
/// `sqlite_session.MIGRATION_4_SQL`.
pub const MIGRATION_4: &str = include_str!("../sql/m04_f4_builder_dm_sessions.sql");
/// `sqlite_campaign.PREDECESSOR_MIGRATION_5_SQL`.
pub const PREDECESSOR_MIGRATION_5: &str = include_str!("../sql/m05_predecessor.sql");
/// `sqlite_campaign.CAMPAIGN_IDENTITY_DELTA_SQL`.
pub const CAMPAIGN_IDENTITY_DELTA: &str = include_str!("../sql/m05_campaign_identity_delta.sql");
/// `sqlite_campaign.LIFECYCLE_EXTENSION_SQL`.
pub const LIFECYCLE_EXTENSION: &str = include_str!("../sql/ext_lifecycle_v3.sql");
/// `sqlite_recovery.RECOVERY_EXTENSION_SQL`.
pub const RECOVERY_EXTENSION: &str = include_str!("../sql/ext_recovery_v1.sql");
/// `sqlite_campaign.PURGE_CAMPAIGN_OPERATIONS_SQL`.
pub const PURGE_CAMPAIGN_OPERATIONS: &str = include_str!("../sql/ext_purge_campaign_operations.sql");
/// `sqlite_campaign.PURGE_EXTENSION_SQL` (hashed; `apply_purge_extension` runs its statements).
pub const PURGE_EXTENSION: &str = include_str!("../sql/ext_purge_v1.sql");
/// `sqlite_state_ownership.MIGRATION_6_SQL`.
pub const MIGRATION_6: &str = include_str!("../sql/m06_state_ownership.sql");
/// `sqlite_mechanics_choice.MIGRATION_7_SQL`.
pub const MIGRATION_7: &str = include_str!("../sql/m07_mechanics_choice.sql");
/// `sqlite_session.MIGRATION_8_SQL`.
pub const MIGRATION_8: &str = include_str!("../sql/m08_standalone_builder_chats.sql");
/// `sqlite_continuity.MIGRATION_9_SQL`.
pub const MIGRATION_9: &str = include_str!("../sql/m09_campaign_state_continuity.sql");
/// `sqlite_draft_lifecycle.MIGRATION_10_SQL`.
pub const MIGRATION_10: &str = include_str!("../sql/m10_unbound_draft_lifecycle.sql");
/// `sqlite_reasoning.MIGRATION_11_SQL`.
pub const MIGRATION_11: &str = include_str!("../sql/m11_private_provider_records.sql");
/// `sqlite_effort.MIGRATION_12_SQL`.
pub const MIGRATION_12: &str = include_str!("../sql/m12_session_reasoning_preferences.sql");
/// The literal schema `sqlite_state_ownership.expected_trigger_identity` builds first.
pub const TRIGGER_FIXTURE: &str = include_str!("../sql/trigger_fixture.sql");

/// `sqlite_campaign.MIGRATION_5_SQL`: the predecessor text followed by the identity delta.
pub fn migration_5() -> String {
    format!("{PREDECESSOR_MIGRATION_5}{CAMPAIGN_IDENTITY_DELTA}")
}
