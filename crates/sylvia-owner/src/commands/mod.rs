//! Owner commands: the command table and one handler module per Python topic.
//!
//! Python's handlers are `interfaces/commands/<topic>.py`. Each Rust handler module here has
//! the same name as its Python file (`campaigns`, `sessions`, `actions`, `providers`,
//! `packages`, `builder`, `character_chat`) and is added when its first command is ported.
//! Handlers take the dispatch context and the request's parameters, call a service operation
//! exported by the lane that owns the service, and return the reply's result. See "Command
//! handlers" in `docs/rust-port/README.md` for who writes what.

pub mod actions;
pub mod builder;
pub mod campaigns;
pub mod packages;
pub mod providers;
pub mod sessions;
mod table;

pub use table::{find, Command, Status, REGISTRY, RUN_LOOP_METHODS};

/// `with self._operation():` around one owner call that reads the store directly: lane 3's
/// `StoreHandle::operation()`, refused once the owner is closing (Python's `RuntimeError`, the
/// owner's `internal_error`).
pub(crate) fn operation(
    ctx: &crate::dispatch::DispatchContext,
) -> sylvia_base::CodedResult<sylvia_store::owner_lifecycle::OperationGuard> {
    ctx.store.operation().map_err(campaigns::store_error)
}
