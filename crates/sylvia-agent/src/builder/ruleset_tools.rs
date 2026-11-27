//! The Builder's Ruleset draft tools (B4b): `agent/_ruleset_draft_check_tool.py` and
//! `agent/_ruleset_test_tool.py`.
//!
//! `check_ruleset_draft` materializes the workspace's `ruleset-draft/` and runs the ordinary
//! package and runtime admission on it. `test_ruleset_draft` runs the materialized archive's
//! saved scenarios in the worker process (`sylvia_app::ruleset_testing`). Neither changes the
//! draft, the library or a campaign. A refusal from either (Python's `AuthorityError`) is the
//! tool's rejection with the validator's correction details; any other exception is the tool's
//! own error.
//!
//! `publish_ruleset_candidate` (`_ruleset_candidate_tool`) answers `rust_port_pending` until
//! B3b's owner seam lands.

use super::is_authority_error;
use super::tools::{ToolHandler, ToolSpec, ToolsetName};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use sylvia_app::package_library::validate_admitted_package_runtimes;
use sylvia_app::package_materializer::materialize_ruleset_draft;
use sylvia_app::ruleset_testing::{run_ruleset_tests_with, WorkerCommand};
use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::DomainError;

/// `cancelled()`: whether the turn's provider work was stopped.
pub type Cancelled = Arc<dyn Fn() -> bool + Send + Sync>;

/// `run_ruleset_tests`'s default timeout, which the test tool keeps.
const TEST_TIMEOUT_SECONDS: f64 = 120.0;

/// `ruleset_validation_failure(error)`: the validator's public correction details, for checking
/// and publication alike.
pub fn ruleset_validation_failure(error: &DomainError) -> JsonMap {
    let mut failure = JsonMap::new();
    failure.insert("success".into(), Json::Bool(false));
    failure.insert("status".into(), "rejected".into());
    failure.insert("code".into(), error.code().unwrap_or("command_rejected").into());
    failure.insert("message".into(), error.message().into());
    let details = error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    failure.insert("validation_errors".into(), Json::Array(details));
    failure
}

/// The refusal of a call with arguments: both tools take none.
fn takes_no_arguments(name: &str) -> Json {
    serde_json::json!({
        "success": false,
        "status": "rejected",
        "code": "builder_tool_parameters_invalid",
        "message": format!("{name} takes no arguments."),
    })
}

/// `except AuthorityError as error: return ruleset_validation_failure(error)`; any other
/// exception escapes the handler, as the tool's error.
fn refused_or_raised(error: DomainError) -> Result<Json, String> {
    if is_authority_error(error.python_class()) {
        Ok(Json::Object(ruleset_validation_failure(&error)))
    } else {
        Err(error.message().to_owned())
    }
}

/// `check(params, context)`: the draft through materialization, archive admission and the
/// runtime checks; the closure identity when all pass.
fn check_draft(workspace: &Path) -> Result<String, DomainError> {
    let materialized = materialize_ruleset_draft(workspace)?;
    let admitted = admit_package_archive(&materialized.archive)?;
    validate_admitted_package_runtimes(&admitted)?;
    Ok(materialized.closure_sha256)
}

/// `ruleset_draft_check_tool(workspace)`: check the fixed Ruleset draft without exporting,
/// installing or modifying its files.
pub fn ruleset_draft_check_tool(workspace: &Path) -> CodedResult<ToolSpec> {
    let workspace = workspace.to_path_buf();
    let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
        if !params.is_empty() {
            return Ok(takes_no_arguments("check_ruleset_draft"));
        }
        match check_draft(&workspace) {
            Ok(closure_sha256) => Ok(serde_json::json!({
                "success": true,
                "status": "valid_draft",
                "closure_sha256": closure_sha256,
                "checked": ["source", "package", "references", "runtime"],
                "published": false,
                "message": "The draft passes package and runtime admission. It remains in the workspace. \
                            This check does not test gameplay sequences.",
            })),
            Err(error) => refused_or_raised(error),
        }
    });
    ToolSpec::from_openai_schema(
        super::turn::exported_definition("check_ruleset_draft"),
        handler,
        &[ToolsetName::BUILDER_TOOLS],
    )
}

/// `ruleset_test_tool(workspace, cancelled=...)`: run the current draft's saved cases. Edits
/// need a fresh run of the new archive.
///
/// `worker` is the worker command, this executable in its worker mode when `None` (Python's
/// `sys.executable`).
pub fn ruleset_test_tool(
    workspace: &Path,
    cancelled: Cancelled,
    worker: Option<WorkerCommand>,
) -> CodedResult<ToolSpec> {
    let workspace: PathBuf = workspace.to_path_buf();
    let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
        if !params.is_empty() {
            return Ok(takes_no_arguments("test_ruleset_draft"));
        }
        let materialized = match materialize_ruleset_draft(&workspace) {
            Ok(materialized) => materialized,
            Err(error) => return refused_or_raised(error),
        };
        let worker = match &worker {
            Some(worker) => worker.clone(),
            None => WorkerCommand::current().map_err(|error| error.to_string())?,
        };
        let cancelled = Arc::clone(&cancelled);
        let check_cancelled = move || cancelled();
        let mut result = match run_ruleset_tests_with(
            &worker,
            &materialized.archive,
            TEST_TIMEOUT_SECONDS,
            Some(&check_cancelled as &dyn Fn() -> bool),
        ) {
            Ok(result) => result,
            Err(error) => return refused_or_raised(error),
        };
        let scenarios = result.get("scenarios").and_then(Json::as_array).map_or(&[][..], Vec::as_slice);
        let total = scenarios.len();
        let passed = scenarios
            .iter()
            .filter(|item| item.get("status").and_then(Json::as_str) == Some("passed"))
            .count();
        let success = result.get("status").and_then(Json::as_str) == Some("passed");
        // `{**result, "success": ..., ...}`: a key the result already has keeps its place.
        result.insert("success".into(), Json::Bool(success));
        result.insert("published".into(), Json::Bool(false));
        result.insert(
            "summary".into(),
            serde_json::json!({"passed": passed, "total": total, "failed": total - passed}),
        );
        result.insert(
            "message".into(),
            "Saved scenarios ran through the campaign loop in disposable state. Inspect failures and coverage \
             before declaring the Ruleset ready. The draft remains editable; no library or campaign was changed."
                .into(),
        );
        Ok(Json::Object(result))
    });
    ToolSpec::from_openai_schema(
        super::turn::exported_definition("test_ruleset_draft"),
        handler,
        &[ToolsetName::BUILDER_TOOLS],
    )
}
