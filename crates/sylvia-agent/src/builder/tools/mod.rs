//! The `tools/` package the Builder runner is built on (lane 1, from lane 4's handover): tool
//! specs (`_spec`), their model-facing JSON (`_converter`), toolsets (`_toolsets`), the registry
//! (`_registry`) and the Builder's workspace tools (`_builder_handlers`, with the runner's
//! `_wrap_builder_handler`).
//!
//! `tools/_dm_campaign_handlers.py` is retired: it serves only the runner's `DM_SESSION` branches.

pub mod glob;
pub mod handlers;
pub mod pattern;
pub mod registry;
pub mod spec;

pub use registry::ToolRegistry;
pub use spec::{tool_spec_to_openai, ToolHandler, ToolParameter, ToolSpec, ToolsetName};
