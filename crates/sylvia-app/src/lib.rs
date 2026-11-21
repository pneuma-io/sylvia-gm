//! Application services above the store: the Rust home of `src/sylvia_gm/application` except
//! `authority.py`.
//!
//! One module per service family, each with a named owning lane in
//! `docs/rust-port/ownership.md`. Add a module only for a family you own.
//!
//! Lane 1 (formats and content):
//! - [`campaign_starting_world`]: a Plan's authored game state with the selected party placed.
//! - [`network_graph`]: visible graph projection and dynamic-intent binding.
//! - [`setting_character_requirements`]: Setting-owned Character File information.
//! - [`srd_lite_weapon_set_migration`]: the SRD Lite 0.2 to 0.3 saved weapon-set migration.
//!
//! Lane 2 (game engine):
//! - [`player_actions`]: the owner marker of a direct player action.

pub mod campaign_starting_world;
pub mod network_graph;
pub mod player_actions;
pub mod setting_character_requirements;
pub mod srd_lite_weapon_set_migration;
