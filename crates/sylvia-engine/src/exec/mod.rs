//! Running a Ruleset's tools: `runtime/_tool_executor.py`, `_effect_executor.py`,
//! `_template_resolver.py`, `_types.py`'s `ExecutionContext` and `ToolResult`, and the
//! in-memory state bridge (`AuthorityMemoryStateBridge`). A mechanic reads an in-memory view of
//! state and returns proposed changes; nothing here touches storage.

pub mod bridge;
pub mod context;
pub mod effects;
pub mod error;
pub mod shape;
pub mod template;
pub mod tool;

pub use bridge::{MemoryStateBridge, StateBridge};
pub use context::{Combat, ExecutionContext};
pub use error::ExecError;
pub use tool::{execute_tool, ExecTool, ToolHost, ToolResult};
