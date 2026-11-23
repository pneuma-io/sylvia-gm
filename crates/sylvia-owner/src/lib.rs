//! The Sylvia GM owner process in Rust (lane 5).
//!
//! The desktop shell starts one owner, sends a launch envelope on stdin, checks the HMAC reply
//! and then exchanges one JSON object per line: requests carry `id`, `method` and `params`,
//! replies carry the same `id`. Replies may come back out of order. This crate replaces
//! `src/sylvia_gm/interfaces/owner_process.py` and is started in the real desktop through the
//! existing `SYLVIA_GM_OWNER_EXECUTABLE` override.
//!
//! What exists today: line protocol, launch through lane 3's `acquire_owner`, request validation, the command table,
//! Python's execution model (inline commands, display reads, model-bound tasks), local
//! diagnostics, desktop drafts, display-load cancellation and `handshake_status`. Every
//! other command still answers `rust_port_pending`.
//! A command is ported by adding its handler under [`commands`] and flipping its status in
//! `commands/table.rs`; `docs/rust-port/commands.md` is the burn-down.

pub mod chat_start;
pub mod claude_settings_cli;
pub mod commands;
pub mod creation_catalog;
pub mod desktop_update;
pub mod diagnostics;
pub mod dictation;
pub mod dispatch;
pub mod display_reads;
pub mod owner_reads;
pub mod preflight;
pub mod preinstall;
pub mod protocol;
pub mod providers;
pub mod run;
pub mod services;
pub mod session_reasoning;
pub mod turn_owner;
pub mod urllib;
pub mod util;
pub mod webui;

pub use run::run;
