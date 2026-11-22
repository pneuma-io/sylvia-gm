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

pub mod argument_contracts;
pub mod campaign_memory;
pub mod provider_resolver;
pub mod provider_session;
pub mod stateless_mcp;
