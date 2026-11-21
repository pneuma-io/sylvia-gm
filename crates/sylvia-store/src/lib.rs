//! Lane 3 (storage and authority): the Rust home of `src/sylvia_gm/infrastructure` and
//! `application/authority.py`.
//!
//! Python's store and authority import each other; here they are modules of one crate with
//! explicit transaction operations. One dedicated thread owns the SQLite connection and takes
//! requests over a channel ([`handle::StoreHandle`]). See `docs/rust-port/README.md`.
//!
//! Ported so far: connection settings, the owner lock, the schema migration chain and the
//! exact schema identity check. The stored-content verifiers and transaction families follow.

pub mod actions;
pub mod authority;
pub mod backup;
pub mod campaigns;
pub mod continuity;
pub mod error;
pub mod handle;
pub mod lanes;
pub mod launch;
pub mod owner_lock;
pub mod packages;
mod participant_history;
pub mod pytext;
pub mod safe_file;
pub mod schema;
pub mod sequel;
pub mod sessions;
pub mod sql;
pub mod store;
pub mod stored;
mod stored_campaigns;
mod stored_construction;
pub mod stored_sessions;
mod verify;

pub use error::{StoreError, StoreResult};
pub use handle::{StoreHandle, StoreThread};
pub use lanes::{LaneGuard, LaneHold, LaneRegistry, OwnerLanes};
pub use store::AuthorityStore;

/// The SQLite library this crate is built with (`sqlite3.sqlite_version` in Python).
pub fn sqlite_version() -> &'static str {
    rusqlite::version()
}
