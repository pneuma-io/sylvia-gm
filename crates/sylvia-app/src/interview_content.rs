//! `application/interview_content.py`: resolve the Interview's questions from the selected
//! Plan's saved world and rules.
//!
//! Ported exactly: the Ruleset-only path and the Setting path when the Setting is in an
//! installed candidate (the exact candidate chosen, the Ruleset's own, or the bundled Rift
//! closure already installed). The branches that need code not yet ported answer
//! `rust_port_pending`, naming the Python function they stop at:
//!
//! - a Plan selection stops at `bundled_campaign_plans.bundled_plan`, which waits on
//!   `plan_snapshot` (lane 2's `tactical_grid`);
//! - installing a bundled snapshot stops at `install_package_archive` (the bundled Rift closure)
//!   or `bundled_campaign_plans.bundled_setting_snapshot` (a bundled Setting).

use crate::bundled_packages::BundledPackages;
use std::path::Path;
use sylvia_base::Json;
use sylvia_domain::authority::PackageRole;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::{campaign_setting_entries, PackageCandidate};
use sylvia_domain::pyops::py_get_attr;

/// The code a branch that needs unported code answers with; no Python owner returns it.
pub const PORT_PENDING: &str = "rust_port_pending";

/// `launch_compatibility.ARCHIVED_RULESET_IDS`.
pub const ARCHIVED_RULESET_IDS: [&str; 1] = ["wotc.srd.5.2.1"];

/// The owner reads `resolve_interview_content` makes (Python's `AuthorityOwner`); the store
/// implements them, and a candidate it does not hold is `CommandRejected` with the code
/// `candidate_unavailable`, as Python's owner raises it.
pub trait InterviewOwner {
    /// `owner.package_candidate(candidate_id)`.
    fn package_candidate(&self, candidate_id: &str) -> DomainResult<PackageCandidate>;
    /// `owner.package_candidates()`, newest first.
    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>>;
}

/// `InterviewContent`.
#[derive(Debug, Clone, PartialEq)]
pub struct InterviewContent {
    pub candidate: Option<PackageCandidate>,
    pub setting_id: Option<String>,
    pub plan_contribution: Option<Json>,
}

fn pending(function: &str) -> DomainError {
    DomainError::command_rejected_with(
        format!("The Rust port does not run {function} yet."),
        PORT_PENDING,
        Vec::new(),
    )
}

/// `launch_compatibility.require_active_ruleset(package_id)`.
pub fn require_active_ruleset(package_id: &str) -> DomainResult<()> {
    if ARCHIVED_RULESET_IDS.contains(&package_id) {
        return Err(DomainError::command_rejected_with(
            "Full is archived; migrate this executable binding to SRD Lite before launch.",
            "archived_ruleset",
            Vec::new(),
        ));
    }
    Ok(())
}

/// `selections.get(name, {}).get(key)`, with Python's `AttributeError` for an answer that is not
/// a mapping.
fn answer<'a>(selections: &'a Json, name: &str, key: &str) -> DomainResult<Option<&'a Json>> {
    let empty = Json::Object(Default::default());
    match py_get_attr(selections, name)? {
        Some(answer) => py_get_attr(answer, key),
        None => py_get_attr(&empty, key).map(|_| None),
    }
}

fn primary_id(candidate: &PackageCandidate) -> DomainResult<String> {
    candidate
        .package_lock()
        .entries()
        .iter()
        .find(|entry| entry.role() == PackageRole::Primary)
        .map(|entry| entry.package_id().to_owned())
        .ok_or_else(|| DomainError::uncaught("StopIteration", ""))
}

fn has_setting(candidate: &PackageCandidate, setting_id: &str) -> bool {
    campaign_setting_entries(candidate.package_lock()).iter().any(|entry| entry.package_id() == setting_id)
}

/// `resolve_interview_content(owner, storage_root, selections)`: prefer contained Plan bytes,
/// then the exact selected Setting, then the source Ruleset.
pub fn resolve_interview_content(
    owner: &dyn InterviewOwner,
    bundled: &BundledPackages,
    _storage_root: Option<&Path>,
    selections: &Json,
) -> DomainResult<InterviewContent> {
    let ruleset_id = answer(selections, "ruleset", "candidate_id")?.filter(|value| !value.is_null());
    let setting_answer = py_get_attr(selections, "setting")?;
    let empty = Json::Object(Default::default());
    let setting_answer = setting_answer.unwrap_or(&empty);
    let setting_id = py_get_attr(setting_answer, "setting_id")?.filter(|value| !value.is_null());
    let plan_id = answer(selections, "plan", "plan_id")?.filter(|value| !value.is_null());
    let Some(ruleset_id) = ruleset_id else {
        if setting_id.is_some() || plan_id.is_some() {
            return Err(DomainError::command_rejected("Selected world content requires its Ruleset"));
        }
        return Ok(InterviewContent { candidate: None, setting_id: None, plan_contribution: None });
    };
    let Some(ruleset_id) = ruleset_id.as_str().filter(|id| !id.is_empty()) else {
        return Err(DomainError::command_rejected("Selected Ruleset identity is invalid"));
    };
    let (selected, logical_ruleset) = match owner.package_candidate(ruleset_id) {
        Ok(candidate) => {
            let logical = primary_id(&candidate)?;
            (Some(candidate), logical)
        }
        Err(error)
            if error.python_class() == "CommandRejected" && error.code() == Some("candidate_unavailable") =>
        {
            (None, ruleset_id.to_owned())
        }
        Err(error) => return Err(error),
    };
    require_active_ruleset(&logical_ruleset)?;
    if let Some(plan_id) = plan_id {
        // `bundled_plan(plan_id) if isinstance(plan_id, str) else None`; a non-text Plan
        // identity then finds no library.
        if !plan_id.is_string() {
            return Err(DomainError::command_rejected("Selected Campaign Plan library is unavailable"));
        }
        return Err(pending("bundled_campaign_plans.bundled_plan"));
    }

    let mut candidate = match py_get_attr(setting_answer, "candidate_id")? {
        Some(Json::String(exact)) => Some(owner.package_candidate(exact)?),
        _ => selected,
    };
    let setting = match setting_id {
        None => None,
        Some(setting_id) => {
            let Some(setting_id) = setting_id.as_str() else {
                return Err(DomainError::command_rejected("Selected Setting identity is invalid"));
            };
            if candidate.is_none() {
                let rift = bundled.bundled_rift_authority()?;
                if logical_ruleset == rift.ruleset.package_id && setting_id == rift.setting.package_id {
                    bundled.bundled_rift_archive()?;
                    candidate = owner
                        .package_candidates()?
                        .into_iter()
                        .find(|item| item.closure_sha256() == rift.closure_sha256);
                    if candidate.is_none() {
                        return Err(pending("install_package_archive"));
                    }
                }
            }
            if !candidate.as_ref().is_some_and(|candidate| has_setting(candidate, setting_id)) {
                return Err(pending("bundled_campaign_plans.bundled_setting_snapshot"));
            }
            Some(setting_id.to_owned())
        }
    };
    if let Some(candidate) = &candidate {
        let primary_matches = candidate
            .package_lock()
            .entries()
            .iter()
            .any(|entry| entry.role() == PackageRole::Primary && entry.package_id() == logical_ruleset);
        if !primary_matches {
            return Err(DomainError::command_rejected("The selected Setting belongs to another Ruleset"));
        }
    }
    Ok(InterviewContent { candidate, setting_id: setting, plan_contribution: None })
}
