//! Lane 1 (formats and content): the Rust home of `src/sylvia_gm/domain`, plus the file-based
//! formats in `state`, `campaign` and `planning`.
//!
//! Identities, package locks, closure hashes, archive admission and content schemas live here.
//! This crate does no I/O beyond reading bytes it is handed, opens no database and starts no
//! async runtime, so the owner, the Builder, content tools and SylviaBench can all call it.
//!
//! Hashed or stored JSON goes through `sylvia_base::pyjson`. See `docs/rust-port/README.md`.
//!
//! - [`authority`]: `domain/authority.py`, identities, roles, package locks and campaign records.
//! - [`package_library`]: `domain/package_library.py`, canonical bytes, closure and content
//!   hashes, package files, portable paths and the strict JSON readers.
//! - [`content`]: content definitions and `validate_content_shape` (JSON Schema Draft 2020-12).
//! - [`error`]: the `ValueError` / `TypeError` the domain raises, and `RulesetDiagnostic`.
//! - [`model_capability`]: `domain/model_capability.py`, capability tiers and model guidance.
//! - [`session`]: `domain/session.py`, Builder and DM session, handoff and plan records.
//! - [`starting_state`], [`inventory`]: package-declared starting state and the shared holding schema.
//! - [`state_ownership`]: `domain/state_ownership.py`, state namespace ownership under a lock.
//! - [`schema_admission`]: `application/schema_admission.py`, the admitted JSON-schema subset.
//! - [`pyops`]: Python `==`, `in`, `set()` and subscripting on JSON values, for untrusted records.

pub mod authority;
pub mod content;
pub mod error;
pub mod inventory;
pub mod model_capability;
pub mod package_library;
pub mod pyops;
pub mod schema_admission;
pub mod session;
pub mod starting_state;
pub mod state_ownership;

pub use error::{DomainError, DomainResult};
