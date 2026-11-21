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
//! - [`package_archive`]: `infrastructure/package_archive.py` and `zip_bounds.py`, with CPython's
//!   `zipfile` read path; [`panel_layout`]: `domain/panel_layout.py`.
//! - [`schema_admission`]: `application/schema_admission.py`, the admitted JSON-schema subset.
//! - [`pyops`]: Python `==`, `in`, `set()` and subscripting on JSON values, for untrusted records.
//! - [`campaign_plan`]: `domain/campaign_plan.py`, the structured Campaign Plan validator.
//! - [`campaign_ending`], [`no_ruleset_plan`]: Campaign Plan endings and No Ruleset worlds.
//! - [`interview_contribution`]: artifact-owned Campaign Interview questions.
//! - [`campaign_sequel`]: appended adventures; [`mechanics_choices`], [`provider_records`],
//!   [`ruleset_tests`]: saved choice records, private provider records and Ruleset test scenarios.

pub mod authority;
pub mod campaign_ending;
pub mod campaign_plan;
pub mod campaign_sequel;
pub mod content;
pub mod error;
pub mod interview_contribution;
pub mod inventory;
pub mod mechanics_choices;
pub mod model_capability;
pub mod no_ruleset_plan;
pub mod package_archive;
pub mod package_library;
pub mod panel_layout;
pub mod provider_records;
pub mod pyops;
pub mod ruleset_tests;
pub mod schema_admission;
pub mod session;
pub mod starting_state;
pub mod state_ownership;

pub use error::{DomainError, DomainResult};
