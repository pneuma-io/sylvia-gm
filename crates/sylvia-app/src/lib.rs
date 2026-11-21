//! Application services above the store: the Rust home of `src/sylvia_gm/application` except
//! `authority.py`.
//!
//! One module per service family, each with a named owning lane in
//! `docs/rust-port/ownership.md`. Add a module only for a family you own.
//!
//! Lane 1 (formats and content):
//! - [`bundled_packages`]: deterministic archives of the first-party bundled packages.
//! - [`campaign_starting_world`]: a Plan's authored game state with the selected party placed.
//! - [`network_graph`]: visible graph projection and dynamic-intent binding.
//! - [`plan_state_admission`]: a Plan's package-owned starting state checked against its Ruleset.
//! - [`package_materialization`]: drafts completed, archived with `zipfile`'s exact bytes, and pre-admitted.
//! - [`setting_character_requirements`]: Setting-owned Character File information.
//! - [`srd_lite_weapon_set_migration`]: the SRD Lite 0.2 to 0.3 saved weapon-set migration.

pub mod bundled_packages;
pub mod campaign_starting_world;
pub mod network_graph;
pub mod package_materialization;
pub mod plan_state_admission;
pub mod setting_character_requirements;
pub mod srd_lite_weapon_set_migration;
