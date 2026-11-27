//! The Ruleset test worker's scenario half (B4a): `agent/_ruleset_test_coverage.py`,
//! `agent/_ruleset_test_runner.py` and `agent/_ruleset_test_provider.py`.
//!
//! The parent that runs the worker in its own process is `sylvia_app::ruleset_testing`; the
//! worker mode that admits the archive and calls [`run_scenario`] for each saved scenario, then
//! [`summarize_coverage`], is lane 5's (`interfaces/ruleset_test_worker.py`).
//!
//! [`run_scenario`] answers `rust_port_pending` once its participants are built: the campaign,
//! its steps and the observations wait on the owner's `create_campaign` and `player_action`,
//! lane 4's session loop (T5) and the context ledger (L1-L3). See [`runner`].

pub mod coverage;
pub mod provider;
pub mod runner;

pub use coverage::{checked_outcomes, summarize_coverage};
pub use provider::{RulesetTestProvider, CALLABLE_LABEL};
pub use runner::{assertions, failure_message, run_scenario, ScenarioDice, ScenarioError};
