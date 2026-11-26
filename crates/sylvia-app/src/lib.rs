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
//! - [`path_guardrail`]: Builder file paths kept inside one allowed root.
//! - [`plan_state_admission`]: a Plan's package-owned starting state checked against its Ruleset.
//! - [`package_materialization`]: drafts completed, archived with `zipfile`'s exact bytes, and pre-admitted.
//! - [`setting_character_requirements`]: Setting-owned Character File information.
//! - [`srd_lite_character_compatibility`]: the retained SRD Lite 0.2 character contract.
//! - [`workspace_files`]: Builder file writes that refuse a stale version.
//! - [`srd_lite_weapon_set_migration`]: the SRD Lite 0.2 to 0.3 saved weapon-set migration.
//!
//! Lane 2 (game engine):
//! - [`package_runtime`]: `validate_package_runtime`, the runtime checks before package publication.
//! - [`player_actions`]: the Ruleset's direct-control policy (`parse_player_controls`).
//! - [`result_card_admission`]: authoring-time result-card and activity-label checks.

pub mod action_sequences;
pub mod actor_decisions;
pub mod authority;
pub mod builder_prompts;
pub mod builder_references;
pub mod builder_session_migration;
pub mod builder_tools;
pub mod bundled_campaign_plans;
pub mod bundled_packages;
pub mod bundled_starter_characters;
pub mod campaign_library_selection;
pub mod campaign_plan_materializer;
pub mod campaign_plan_sources;
pub mod campaign_rift;
pub mod campaign_starting_world;
pub mod character_profiles;
pub mod character_rules;
pub mod compatible_character_copy;
pub mod decision_scope;
pub mod initial_character_state;
pub mod interview_content;
pub mod knowledge_search;
pub mod launch_compatibility;
pub mod library_sources;
pub mod map_segment_preview;
pub mod network_graph;
pub mod package_library;
pub mod package_materialization;
pub mod package_materializer;
pub mod package_mechanic;
pub mod package_record_edits;
pub mod package_runtime;
pub mod path_guardrail;
pub mod piece_table;
pub mod plan_network_admission;
pub mod plan_snapshot;
pub mod plan_spatial_admission;
pub mod plan_state_admission;
pub mod player_actions;
pub mod result_card_admission;
pub mod result_cards;
pub mod reusable_library_deletion;
pub mod reusable_participants;
pub mod reusable_plans;
pub mod ruleless_play;
pub mod ruleset_source;
pub mod setting_character_requirements;
pub mod spatial_preview;
pub mod srd_lite_character_compatibility;
pub mod srd_lite_weapon_set_migration;
pub mod tactical_grid;
pub mod workspace_files;
