//! `application/interview_content.py`: resolve the Interview's questions from the selected
//! Plan's saved world and rules.
//!
//! The owner is builder_references' [`ReferenceOwner`] (`LibraryOwner`'s reads plus
//! `install_package_archive`), which `AuthorityStore` implements: a saved Plan is validated
//! against its retained packages through those reads, and a bundled snapshot is installed with it.
//! A candidate the owner does not hold is `CommandRejected` with the code `candidate_unavailable`,
//! as Python's owner raises it.

use crate::builder_references::{DomainOwner, OwnerError, ReferenceOwner};
use crate::bundled_packages::BundledPackages;
use crate::campaign_plan_sources::validate_retained_campaign_plan;
use crate::reusable_plans::load_reusable_campaign_plan;
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::Json;
use sylvia_domain::authority::PackageRole;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::{campaign_setting_entries, PackageCandidate};
use sylvia_domain::pyops::{py_eq, py_get_attr, py_item, py_str};

/// `launch_compatibility.ARCHIVED_RULESET_IDS`.
pub const ARCHIVED_RULESET_IDS: [&str; 1] = ["wotc.srd.5.2.1"];

/// `InterviewContent`.
#[derive(Debug, Clone, PartialEq)]
pub struct InterviewContent {
    pub candidate: Option<PackageCandidate>,
    pub setting_id: Option<String>,
    pub plan_contribution: Option<Json>,
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

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected(message)
}

/// `owner.install_package_archive(io.BytesIO(snapshot), install_id=f"interview-snapshot-...")`.
fn install_snapshot<O: ReferenceOwner>(owner: &O, snapshot: &[u8]) -> Result<PackageCandidate, O::Error> {
    owner.install_package_archive(snapshot, &format!("interview-snapshot-{}", &sha256_hex(snapshot)[..24]))
}

/// `resolve_interview_content(owner, storage_root, selections)`: prefer contained Plan bytes,
/// then the exact selected Setting, then the source Ruleset.
pub fn resolve_interview_content<O: ReferenceOwner>(
    owner: &O,
    bundled: &BundledPackages,
    storage_root: Option<&Path>,
    selections: &Json,
) -> Result<InterviewContent, O::Error> {
    let ruleset_id = answer(selections, "ruleset", "candidate_id")?.filter(|value| !value.is_null());
    let setting_answer = py_get_attr(selections, "setting")?;
    let empty = Json::Object(Default::default());
    let setting_answer = setting_answer.unwrap_or(&empty);
    let setting_id = py_get_attr(setting_answer, "setting_id")?.filter(|value| !value.is_null());
    let plan_id = answer(selections, "plan", "plan_id")?.filter(|value| !value.is_null());
    let Some(ruleset_id) = ruleset_id else {
        if setting_id.is_some() || plan_id.is_some() {
            return Err(rejected("Selected world content requires its Ruleset").into());
        }
        return Ok(InterviewContent { candidate: None, setting_id: None, plan_contribution: None });
    };
    let Some(ruleset_id) = ruleset_id.as_str().filter(|id| !id.is_empty()) else {
        return Err(rejected("Selected Ruleset identity is invalid").into());
    };
    let (selected, logical_ruleset) = match owner.package_candidate(ruleset_id) {
        Ok(candidate) => {
            let logical = primary_id(&candidate)?;
            (Some(candidate), logical)
        }
        Err(error) => {
            let domain = error.as_domain();
            if domain.python_class() != "CommandRejected" || domain.code() != Some("candidate_unavailable") {
                return Err(error);
            }
            (None, ruleset_id.to_owned())
        }
    };
    require_active_ruleset(&logical_ruleset)?;
    if let Some(plan_id) = plan_id {
        let builtin = match plan_id {
            Json::String(plan_id) => bundled.bundled_plan(plan_id)?,
            _ => None,
        };
        let (plan, mut snapshot, contribution) = match builtin {
            Some(builtin) => (builtin.plan, builtin.package_snapshot, builtin.interview_contribution),
            None => {
                let (Some(storage_root), Json::String(plan_id)) = (storage_root, plan_id) else {
                    return Err(rejected("Selected Campaign Plan library is unavailable").into());
                };
                let domain_owner = DomainOwner(owner);
                let validate = |payload: &Json, markdown: &str| {
                    validate_retained_campaign_plan(&domain_owner, bundled, payload, markdown, None)
                };
                let saved = load_reusable_campaign_plan(storage_root, plan_id, Some(&validate), bundled)?;
                (saved.plan, saved.package_snapshot, saved.interview_contribution)
            }
        };
        let payload = Json::Object(plan.payload.clone());
        let bindings = py_item(&payload, "bindings")?;
        let setting_value = setting_id.cloned().unwrap_or(Json::Null);
        if !py_eq(py_item(py_item(bindings, "ruleset")?, "id")?, &Json::from(logical_ruleset.as_str()))
            || !py_eq(py_item(py_item(bindings, "campaign_setting")?, "id")?, &setting_value)
        {
            return Err(rejected("The selected Plan belongs to different content").into());
        }
        let expected = py_item(bindings, "package_closure_sha256")?;
        let mut candidate = owner
            .package_candidates()?
            .into_iter()
            .find(|item| py_eq(&Json::from(item.closure_sha256()), expected));
        if candidate.is_none() && snapshot.is_none() {
            snapshot = bundled.bundled_rift_archive_for_closure(expected)?;
        }
        if let (None, Some(snapshot)) = (&candidate, &snapshot) {
            candidate = Some(install_snapshot(owner, snapshot)?);
        }
        let Some(candidate) = candidate.filter(|item| py_eq(&Json::from(item.closure_sha256()), expected))
        else {
            return Err(rejected("Selected Campaign Plan snapshot is unavailable").into());
        };
        return Ok(InterviewContent {
            candidate: Some(candidate),
            setting_id: Some(py_str(&setting_value)),
            plan_contribution: contribution.map(Json::Object),
        });
    }

    let mut candidate = match py_get_attr(setting_answer, "candidate_id")? {
        Some(Json::String(exact)) => Some(owner.package_candidate(exact)?),
        _ => selected,
    };
    let setting = match setting_id {
        None => None,
        Some(setting_id) => {
            let Some(setting_id) = setting_id.as_str() else {
                return Err(rejected("Selected Setting identity is invalid").into());
            };
            if candidate.is_none() {
                let rift = bundled.bundled_rift_authority()?;
                if logical_ruleset == rift.ruleset.package_id && setting_id == rift.setting.package_id {
                    let snapshot = bundled.bundled_rift_archive()?;
                    let closure = rift.closure_sha256.clone();
                    candidate =
                        owner.package_candidates()?.into_iter().find(|item| item.closure_sha256() == closure);
                    if candidate.is_none() {
                        candidate = Some(install_snapshot(owner, &snapshot)?);
                    }
                }
            }
            if !candidate.as_ref().is_some_and(|candidate| has_setting(candidate, setting_id)) {
                if let Some(snapshot) = bundled.bundled_setting_snapshot(&logical_ruleset, setting_id)? {
                    candidate = Some(install_snapshot(owner, &snapshot)?);
                }
            }
            if !candidate.as_ref().is_some_and(|candidate| has_setting(candidate, setting_id)) {
                return Err(rejected("Selected Setting snapshot is unavailable; refresh its choices").into());
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
            return Err(rejected("The selected Setting belongs to another Ruleset").into());
        }
    }
    Ok(InterviewContent { candidate, setting_id: setting, plan_contribution: None })
}
