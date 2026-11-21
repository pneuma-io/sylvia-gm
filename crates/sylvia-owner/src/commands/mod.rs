//! Owner commands: the command table and one handler module per Python topic.
//!
//! Python's handlers are `interfaces/commands/<topic>.py`. Each Rust handler module here has
//! the same name as its Python file (`campaigns`, `sessions`, `actions`, `providers`,
//! `packages`, `builder`, `character_chat`) and is added when its first command is ported.
//! Handlers take the dispatch context and the request's parameters, call a service operation
//! exported by the lane that owns the service, and return the reply's result. See "Command
//! handlers" in `docs/rust-port/README.md` for who writes what.

pub mod builder;
pub mod campaigns;
pub mod providers;
mod table;

pub use table::{find, Command, Status, REGISTRY, RUN_LOOP_METHODS};
