//! Lane 4 (agents): the Rust home of `src/sylvia_gm/agent`, `session` and `tools`.
//!
//! GM and Builder orchestration, the context ledger, tool definitions, continuation and
//! interruption. Long texts the model reads are exported data files; short ones are literals
//! copied from the Python source byte for byte.
//! See `docs/rust-port/README.md`.
//!
//! - [`provider_resolver`] holds `agent/_provider_resolver.py`.
//! - [`stateless_mcp`] holds `ModelMechanicsTool` from `agent/_stateless_mcp.py`.
//! - [`campaign_memory`] holds `agent/_campaign_memory.py`.
//! - [`argument_contracts`] holds the projection half of `agent/_argument_contracts.py`.
//! - [`provider_session`] holds parts of `agent/_provider_session.py`.
//! - [`provider_turn`] holds `agent/_provider_turn_state.py` and `agent/_provider_turn_decisions.py`.
//! - [`session_loop`] holds parts of `agent/_session_loop.py`.
//! - [`activity`] holds `agent/_activity.py`.
//! - [`working_context`] holds `agent/_working_context.py`.
//! - [`request_data`] holds `agent/_request_data.py`.

pub mod activity;
pub mod argument_contracts;
pub mod campaign_memory;
pub mod provider_resolver;
pub mod provider_session;
pub mod provider_turn;
pub mod request_data;
pub mod session_loop;
pub mod stateless_mcp;
pub mod working_context;
