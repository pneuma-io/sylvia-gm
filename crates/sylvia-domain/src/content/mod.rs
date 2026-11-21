//! Content definitions and their validation.
//!
//! - [`catalog`]: `domain/content_definitions.py` and the `_content_schema_*` modules, as data.
//! - [`validation`]: `domain/content_validation.py`, `validate_content_shape`.
//! - [`schema`]: the JSON Schema Draft 2020-12 engine behind it, with `jsonschema`'s errors.
//! - [`pyregex`]: Python `re.search` for the definitions' patterns.

pub mod catalog;
pub mod pyregex;
pub mod schema;
pub mod validation;

pub use catalog::{get_content_definition, list_content_definitions};
pub use validation::{shape_diagnostics, validate_content_shape, ContentShapeError};

/// `RULESET_TESTS_PATH` from `domain/_content_schema_ruleset_tests.py`.
pub const RULESET_TESTS_PATH: &str = "tests/scenarios.json";
