//! The Sylvia GM owner process in Rust (lane 5).
//!
//! The desktop shell starts one owner, sends a launch envelope on stdin, checks the HMAC reply
//! and then exchanges one JSON object per line: requests carry `id`, `method` and `params`,
//! replies carry the same `id`. Replies may come back out of order. This crate replaces
//! `src/sylvia_gm/interfaces/owner_process.py` and is started in the real desktop through the
//! existing `SYLVIA_GM_OWNER_EXECUTABLE` override.
//!
//! What exists today is the frame: launch handshake, line protocol, request validation, the
//! command table and shutdown. Every command in the table still answers `rust_port_pending`.
//! A command is ported by adding its handler in [`dispatch`] and flipping its status in
//! `commands.rs`; `docs/rust-port/commands.md` is the burn-down.

pub mod commands;
pub mod dispatch;
pub mod handshake;
pub mod protocol;
pub mod run;

pub use run::run;
