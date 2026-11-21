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

pub mod authority;
pub mod content;
pub mod error;
pub mod model_capability;
pub mod package_library;
pub mod session;

pub use error::{DomainError, DomainResult};
