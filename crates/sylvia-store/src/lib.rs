//! Lane 3 (storage and authority): the Rust home of `src/sylvia_gm/infrastructure` and
//! `application/authority.py`.
//!
//! Python's store and authority import each other; here they are modules of one crate with
//! explicit transaction operations. One dedicated thread owns the SQLite connection and takes
//! requests over a channel ([`handle::StoreHandle`]). See `docs/rust-port/README.md`.
//!
//! Ported so far: connection settings, the owner lock, the schema migration chain and the
//! exact schema identity check. The stored-content verifiers and transaction families follow.

pub mod action_execution;
pub mod actions;
pub mod archive_validation;
pub mod authority;
pub mod backup;
pub mod builder_launch_migration;
pub mod builder_sources;
pub mod campaign_archive;
pub mod campaigns;
pub mod catalog_reads;
pub mod continuity;
pub mod error;
pub mod export;
pub mod export_writer;
pub mod f5;
pub mod guided;
pub mod guided_advance;
pub mod guided_finalize;
pub mod guided_start;
pub mod handle;
pub mod interruptions;
pub mod lanes;
pub mod launch;
pub mod lifecycle;
pub mod mechanics_choices;
pub mod narration;
pub mod owner_lifecycle;
pub mod owner_lock;
mod owner_sessions;
pub mod package_reads;
pub mod packages;
mod participant_history;
mod player_reads;
pub mod private_records;
pub mod purge;
pub mod pytext;
pub mod reasoning;
pub mod recovery;
pub mod recovery_graph;
pub mod recovery_staging;
pub mod safe_file;
pub mod schema;
pub mod sequel;
pub mod sessions;
pub mod snapshots;
pub mod sql;
pub mod store;
pub mod stored;
mod stored_campaigns;
mod stored_construction;
pub mod stored_sessions;
pub mod tool_cache;
mod verify;
pub mod work;

pub use error::{StoreError, StoreResult};
pub use handle::{StoreHandle, StoreThread};
pub use lanes::{LaneGuard, LaneHold, LaneRegistry, OwnerLanes};
pub use owner_sessions::SessionWork;
pub use store::AuthorityStore;
pub use stored_construction::OperationResult;

/// The SQLite library this crate is built with (`sqlite3.sqlite_version` in Python).
pub fn sqlite_version() -> &'static str {
    rusqlite::version()
}
