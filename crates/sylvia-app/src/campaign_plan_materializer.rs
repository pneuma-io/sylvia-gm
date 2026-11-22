//! `builder/_campaign_plan_materializer.py`: validate the fixed Builder Campaign Plan draft
//! without scanning unrelated workspace data, then publish its canonical handoff files.

use crate::package_materializer::reject_reparse_chain;
use crate::plan_state_admission::validate_starting_declarations;
use crate::workspace_files::os_error;
use std::collections::BTreeSet;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use sylvia_base::pyjson::{loads_hooked, HookFailure, HookPolicy};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::campaign_plan::{
    validate_campaign_plan, CampaignPlanExpectations, PackageBinding, ValidatedCampaignPlan,
    CAMPAIGN_PLAN_BYTE_LIMIT,
};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_utf8_decode;
use sylvia_domain::starting_state::parse_state_schemas;

const DRAFT_DIRECTORY: &str = "campaign-plan-draft";
const PLAN_FILE: &str = "plan.json";
const READABLE_FILE: &str = "plan.md";

/// `CampaignPlanDraft`: one validated Builder artifact ready for durable publication evidence.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignPlanDraft {
    pub plan: ValidatedCampaignPlan,
    pub plan_path: String,
    pub readable_path: String,
}

/// The expectations both entry points take (`expected_ruleset`, `expected_setting`,
/// `expected_closure_sha256`, `setting_paths`, `setting_knowledge_ids`).
#[derive(Debug, Clone, Copy)]
pub struct PlanDraftExpectations<'a> {
    pub expected_ruleset: &'a PackageBinding,
    pub expected_setting: &'a PackageBinding,
    pub expected_closure_sha256: &'a str,
    pub setting_paths: &'a BTreeSet<String>,
    pub setting_knowledge_ids: &'a BTreeSet<String>,
}

/// `_bounded_text(path, label)`: one regular UTF-8 draft file under the Campaign Plan ceiling.
fn bounded_text(path: &Path, label: &str) -> DomainResult<String> {
    let is_link = std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink());
    if is_link || !path.is_file() {
        return Err(DomainError::value(format!("{label} is unavailable")));
    }
    let mut value = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(CAMPAIGN_PLAN_BYTE_LIMIT as u64 + 1).read_to_end(&mut value))
        .map_err(|error| os_error(&error, path))?;
    if value.len() > CAMPAIGN_PLAN_BYTE_LIMIT {
        return Err(DomainError::value(format!("{label} is too large")));
    }
    py_utf8_decode(&value).map_err(|message| DomainError::Diagnosed {
        error_type: "UnicodeDecodeError",
        message,
        diagnostics: Vec::new(),
    })
}

/// `json.loads(plan_text, object_pairs_hook=_strict_object, parse_constant=...)`, then Python's
/// refusal of a decode error.
fn read_plan(text: &str) -> DomainResult<Json> {
    match loads_hooked(text, HookPolicy { refuse_duplicates: true, refuse_constants: true }) {
        Ok(hooked) if !hooked.non_finite => Ok(hooked.value),
        // Differences row 2: Python reads an overflowing number as `inf` and validates on; a
        // Rust value cannot hold it, so the text is refused as invalid JSON.
        Ok(_) => Err(DomainError::value("Campaign Plan JSON is invalid")),
        Err(HookFailure::Decode(_)) => Err(DomainError::value("Campaign Plan JSON is invalid")),
        Err(HookFailure::Duplicate(_)) => {
            Err(DomainError::value("Campaign Plan JSON contains a duplicate key"))
        }
        Err(HookFailure::Constant(constant)) => {
            Err(DomainError::value(format!("Campaign Plan JSON constant {constant} is invalid")))
        }
        // Differences rows 1, 3 and 4: beyond 64-bit integers, lone surrogates and nesting of
        // 128 levels or more are refused by the declared limits.
        Err(HookFailure::Unsupported(_)) => Err(DomainError::value("Campaign Plan JSON is invalid")),
    }
}

/// `materialize_campaign_plan_draft(workspace, ...)`: validate exactly
/// `campaign-plan-draft/plan.json` and `plan.md`.
pub fn materialize_campaign_plan_draft(
    workspace: &Path,
    expected: PlanDraftExpectations<'_>,
) -> DomainResult<CampaignPlanDraft> {
    let draft = workspace.join(DRAFT_DIRECTORY);
    let plan_text = bounded_text(&draft.join(PLAN_FILE), "Campaign Plan structured artifact")?;
    let readable = bounded_text(&draft.join(READABLE_FILE), "Campaign Plan readable rendering")?;
    let payload = read_plan(&plan_text)?;
    let plan = validate_campaign_plan(
        &payload,
        &readable,
        CampaignPlanExpectations {
            expected_ruleset: Some(expected.expected_ruleset),
            expected_setting: Some(expected.expected_setting),
            expected_closure_sha256: expected.expected_closure_sha256,
            setting_paths: expected.setting_paths,
            setting_knowledge_ids: expected.setting_knowledge_ids,
        },
    )?;
    Ok(CampaignPlanDraft {
        plan,
        plan_path: format!("{DRAFT_DIRECTORY}/{PLAN_FILE}"),
        readable_path: format!("{DRAFT_DIRECTORY}/{READABLE_FILE}"),
    })
}

/// `publish_campaign_plan_draft(workspace, ..., state_schemas=...)`: validate a draft, then
/// publish the exact canonical files the DM handoff uses.
pub fn publish_campaign_plan_draft(
    workspace: &Path,
    expected: PlanDraftExpectations<'_>,
    state_schemas: &[JsonMap],
) -> DomainResult<CampaignPlanDraft> {
    let draft = materialize_campaign_plan_draft(workspace, expected)?;
    if !state_schemas.is_empty() {
        let schemas =
            parse_state_schemas(&Json::Array(state_schemas.iter().cloned().map(Json::Object).collect()))?;
        validate_starting_declarations(
            &draft.plan.payload,
            &expected.expected_ruleset.package_id,
            &expected.expected_ruleset.version,
            &schemas,
        )?;
    }
    publish_campaign_plan_files(workspace, draft.plan)
}

/// `tempfile.NamedTemporaryFile(dir=workspace, prefix=".plan-save-", delete=False)`.
fn temporary_file(directory: &Path) -> std::io::Result<(PathBuf, std::fs::File)> {
    const CHARACTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    let mut seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos() as u64)
        .unwrap_or_default()
        ^ (u64::from(std::process::id()) << 32);
    for _ in 0..100 {
        let name: String = (0..8)
            .map(|_| {
                seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
                CHARACTERS[(seed >> 33) as usize % CHARACTERS.len()] as char
            })
            .collect();
        let path = directory.join(format!(".plan-save-{name}"));
        match std::fs::OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(std::io::Error::new(std::io::ErrorKind::AlreadyExists, "No usable temporary file name found"))
}

/// `publish_campaign_plan_files(workspace, plan)`: write the canonical handoff files only after
/// the complete Plan has been validated.
pub fn publish_campaign_plan_files(
    workspace: &Path,
    plan: ValidatedCampaignPlan,
) -> DomainResult<CampaignPlanDraft> {
    let outputs = [
        (workspace.join("campaign-plan.json"), canonical_json_bytes(&Json::Object(plan.payload.clone()))),
        (workspace.join("campaign-plan.md"), plan.readable_markdown.as_bytes().to_vec()),
    ];
    for (target, content) in outputs {
        let present =
            target.exists() || std::fs::symlink_metadata(&target).is_ok_and(|m| m.file_type().is_symlink());
        let checked =
            if present { target.clone() } else { target.parent().unwrap_or(workspace).to_path_buf() };
        reject_reparse_chain(workspace, &checked)?;
        let mut temporary: Option<PathBuf> = None;
        let written = (|| -> std::io::Result<()> {
            let (path, mut file) = temporary_file(workspace)?;
            temporary = Some(path.clone());
            file.write_all(&content)?;
            drop(file);
            std::fs::rename(&path, &target)
        })();
        if let Some(path) = &temporary {
            let _ = std::fs::remove_file(path);
        }
        written.map_err(|error| os_error(&error, &target))?;
    }
    Ok(CampaignPlanDraft {
        plan,
        plan_path: "campaign-plan.json".into(),
        readable_path: "campaign-plan.md".into(),
    })
}
