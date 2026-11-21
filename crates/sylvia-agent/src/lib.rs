//! Lane 4 (agents): the Rust home of `src/sylvia_gm/agent`, `session` and `tools`.
//!
//! GM and Builder orchestration, the context ledger, tool definitions, continuation and
//! interruption. Text the model reads is loaded from shared data files, never retyped here.
//! See `docs/rust-port/README.md`.
//!
//! - [`provider_resolver`] holds `agent/_provider_resolver.py`.

pub mod provider_resolver;
