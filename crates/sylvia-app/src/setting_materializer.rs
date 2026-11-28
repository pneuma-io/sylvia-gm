//! `builder/_setting_materializer.py` and `builder/_setting_draft_check.py`: an authored world
//! published with an owned, optionally customized Ruleset copy (`materialize_setting_draft`),
//! and the same path checked without installing anything (`check_setting_draft`).

use crate::campaign_library_selection::SelectionOwner;
use crate::package_library::validate_admitted_package_runtimes;
use crate::package_materialization::{
    materialize_package_archive, MaterializedPackageArchive, NormalizedPackageDraft, Payloads,
};
use crate::package_materializer::{read_payloads, reject_reparse_chain, resolve};
use crate::package_record_edits::apply_package_record_edits;
use crate::package_snapshots::{ruleset_copy, select_snapshot_sources};
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_item;

/// The declarations file `_world_draft` writes when the author supplied none.
const DECLARATIONS_PATH: &str = "content/content-declarations.json";

/// `_world_draft(package_id=..., version=..., title=..., payloads=..., ruleset=...)`: the
/// authored world files indexed, without inventing mechanics or completion claims.
fn world_draft(
    package_id: &str,
    version: &str,
    title: &str,
    payloads: Payloads,
    ruleset: &NormalizedPackageDraft,
) -> sylvia_domain::DomainResult<NormalizedPackageDraft> {
    let mut files = payloads;
    let mut digests: Vec<(&String, String)> =
        files.iter().map(|(path, value)| (path, sha256_hex(value))).collect();
    digests.sort();
    let index: JsonMap =
        digests.into_iter().map(|(path, digest)| (path.clone(), Json::from(digest))).collect();
    let source_sha = sha256_hex(canonical_json_bytes(&Json::Object(index)));
    if !files.iter().any(|(path, _)| path == DECLARATIONS_PATH) {
        let mut artifacts: Vec<String> = files.iter().map(|(path, _)| path.clone()).collect();
        artifacts.sort();
        let declarations = serde_json::json!({
            "format_id": "sylvia-gm-package-content-declarations",
            "format_version": 1,
            "scope": {
                "scope_id": format!("{package_id}.world"),
                "scope_kind": "bounded_package",
                "claim_status": "NOT_COMPLETE",
                "source_boundary_refs": [],
            },
            "source_authorities": [{
                "id": format!("{package_id}.authored-files"),
                "document_id": format!("{package_id}.source-file-index"),
                "version": version,
                "media_type": "application-json",
                "content_sha256": source_sha,
                "license_id": "NOASSERTION",
            }],
            "inclusions": [{
                "id": format!("{package_id}.world"),
                "kind": "campaign_setting",
                "family": "world",
                "disposition": "included_nonmechanical",
                "classifications": ["authored_content"],
                "sources": [],
                "links": {
                    "artifacts": artifacts,
                    "capabilities": [],
                    "mechanics": [],
                    "procedures": [],
                    "prompt_fragments": [],
                    "schemas": [],
                    "state_namespaces": [],
                    "tools": [],
                    "ui_surfaces": [],
                },
                "evidence": {"tests": []},
            }],
            "exclusions": [],
        });
        files.push((DECLARATIONS_PATH.to_owned(), canonical_json_bytes(&declarations)));
    }
    let ruleset_record = Json::Object(ruleset.record.clone());
    let record = serde_json::json!({
        "id": package_id,
        "version": version,
        "role": "dependency",
        "content_kind": "campaign_setting",
        "metadata": {"label": title},
        "provenance": {"publisher": "Local Builder", "source_sha256": source_sha},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": py_item(&ruleset_record, "minimum_model_capability_tier")?,
        "dependencies": [{
            "namespace": "ruleset_snapshot",
            "kind": "ruleset_snapshot",
            "id": py_item(&ruleset_record, "id")?,
            "version": py_item(&ruleset_record, "version")?,
        }],
        "capabilities": [],
        "tools": [],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "participant_artifacts": [],
        "content_declarations": DECLARATIONS_PATH,
    });
    let Json::Object(record) = record else { unreachable!("built as an object") };
    Ok(NormalizedPackageDraft::new(record, files, 4))
}

/// `{**ruleset.payloads, **changed}`: a replaced file keeps its place, a new one comes last.
fn merged(mut payloads: Payloads, changed: Payloads) -> Payloads {
    for (path, value) in changed {
        match payloads.iter_mut().find(|(existing, _)| *existing == path) {
            Some(entry) => entry.1 = value,
            None => payloads.push((path, value)),
        }
    }
    payloads
}

/// `materialize_setting_draft(owner, workspace, candidate_id=..., package_id=..., version=...,
/// title=...)`: one complete world and its contained rules, admitted before anything enters the
/// picker.
pub fn materialize_setting_draft<O: SelectionOwner>(
    owner: &O,
    workspace: &Path,
    candidate_id: &str,
    package_id: &str,
    version: &str,
    title: &str,
) -> Result<MaterializedPackageArchive, O::Error> {
    let workspace = resolve(workspace);
    let root = workspace.join("setting-draft");
    reject_reparse_chain(&workspace, &root.join("files"))?;
    let files = read_payloads(&root.join("files"))?;
    let drafts = ruleset_copy(owner, candidate_id)?;
    let mut drafts = select_snapshot_sources(owner, &workspace, &root, drafts)?;
    let replacements = root.join("ruleset-files");
    if replacements.exists() {
        reject_reparse_chain(&workspace, &replacements)?;
        let changed = read_payloads(&replacements)?;
        let ruleset = drafts[0].clone();
        drafts[0] = NormalizedPackageDraft::new(
            ruleset.record,
            merged(ruleset.payloads, changed),
            ruleset.format_version,
        );
    }
    let mut drafts = apply_package_record_edits(&workspace, &root, &drafts)?;
    let world = world_draft(package_id, version, title, files, &drafts[0])?;
    drafts.push(world);
    Ok(materialize_package_archive(&drafts)?)
}

/// `check_setting_draft(owner, workspace, candidate_id=..., package_id=..., version=...,
/// title=...)`: one Setting draft validated through publication's admission path, without
/// installing it or changing its workspace.
pub fn check_setting_draft<O: SelectionOwner>(
    owner: &O,
    workspace: &Path,
    candidate_id: &str,
    package_id: &str,
    version: &str,
    title: &str,
) -> Result<JsonMap, O::Error> {
    let materialized = materialize_setting_draft(owner, workspace, candidate_id, package_id, version, title)?;
    let admitted = admit_package_archive(&materialized.archive)?;
    validate_admitted_package_runtimes(&admitted)?;
    let mut answer = JsonMap::new();
    answer.insert("closure_sha256".into(), materialized.closure_sha256.into());
    answer.insert(
        "checked".into(),
        serde_json::json!(["setting", "contained_ruleset", "package", "references", "runtime"]),
    );
    answer.insert("published".into(), false.into());
    answer.insert("state_changed".into(), false.into());
    Ok(answer)
}
