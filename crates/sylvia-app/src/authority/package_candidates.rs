//! `AuthorityOwner.inspect_package_candidate`, `package_candidate_text` and
//! `compare_package_candidates`: static reads of one installed package candidate's exact closure,
//! never campaign data.

use crate::package_runtime::{load_bounded_runtime_yaml, PackageRuntimeFailure, PACKAGE_RUNTIME_BYTE_LIMIT, PACKAGE_RUNTIME_PATH};
use std::collections::BTreeSet;
use sylvia_base::pyyaml::Data;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::package_library::{parse_gameplay_support_bytes, GAMEPLAY_SUPPORT_PATH};
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

fn rejected_with(message: &str, code: &str) -> StoreError {
    DomainError::command_rejected_with(message, code, Vec::new()).into()
}

fn rejected(message: &str) -> StoreError {
    DomainError::command_rejected(message).into()
}

fn object(pairs: Vec<(&str, Json)>) -> Json {
    Json::Object(pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

fn texts<'a>(items: impl IntoIterator<Item = &'a str>) -> Json {
    Json::Array(items.into_iter().map(|item| Json::String(item.to_owned())).collect())
}

fn optional(value: &Option<String>) -> Json {
    value.clone().map_or(Json::Null, Json::String)
}

/// `sorted(map(str, mapping))` for a runtime table. Admission refused every key that is not
/// text (`Package runtime keys must be strings`), so an installed runtime's keys are all text.
fn table_keys(table: &[(Data, Data)]) -> StoreResult<Vec<String>> {
    let mut keys = Vec::with_capacity(table.len());
    for (key, _) in table {
        let Data::Str(text) = key else {
            return Err(StoreError::Other("Installed package runtime has a key that is not text".into()));
        };
        keys.push(text.clone());
    }
    keys.sort();
    Ok(keys)
}

/// The runtime's private mechanics (its tools that the manifest does not declare) and its
/// procedures, from a raw `yaml.load(..., Loader=_BoundedRuntimeLoader)`. Python lets a load
/// failure through; an admitted runtime always loads.
fn runtime_names(runtime_bytes: &[u8], public: &BTreeSet<&str>) -> StoreResult<(Vec<String>, Vec<String>)> {
    let runtime = match load_bounded_runtime_yaml(runtime_bytes) {
        Ok(runtime) => runtime,
        Err(PackageRuntimeFailure::Rejected(error)) => {
            return Err(DomainError::uncaught("PackageRuntimeError", error.message).into())
        }
        Err(PackageRuntimeFailure::Uncaught { class, message }) => {
            return Err(DomainError::uncaught(class, message).into())
        }
    };
    let (mut private_mechanics, mut procedures) = (Vec::new(), Vec::new());
    if let Data::Dict(_) = &runtime {
        if let Some(Data::Dict(tools)) = runtime.get_str("tools") {
            let names: BTreeSet<String> = table_keys(tools)?.into_iter().collect();
            private_mechanics = names.into_iter().filter(|name| !public.contains(name.as_str())).collect();
        }
        if let Some(Data::Dict(table)) = runtime.get_str("procedures") {
            procedures = table_keys(table)?;
        }
    }
    Ok((private_mechanics, procedures))
}

fn inspect_on(store: &AuthorityStore, candidate_id: &str) -> StoreResult<JsonMap> {
    let candidate = store.package_candidate(candidate_id)?;
    let mut packages = Vec::new();
    for entry in candidate.package_lock().entries() {
        let manifest = store.exact_package_manifest(entry.package_id(), entry.version(), entry.sha256())?;
        let mut support_counts = JsonMap::new();
        let mut support_records = Vec::new();
        let support_bytes = store.package_file_bytes(
            entry.package_id(),
            entry.version(),
            entry.sha256(),
            GAMEPLAY_SUPPORT_PATH,
            2 * 1024 * 1024,
        )?;
        if let Some(bytes) = support_bytes {
            let support = parse_gameplay_support_bytes(&bytes)?;
            for item in &support.records {
                let count = support_counts.get(&item.gameplay_resolution).and_then(Json::as_i64).unwrap_or(0);
                support_counts.insert(item.gameplay_resolution.clone(), (count + 1).into());
                support_records.push(object(vec![
                    ("id", item.identity.clone().into()),
                    ("reference_availability", item.reference_availability.clone().into()),
                    ("gameplay_resolution", item.gameplay_resolution.clone().into()),
                    ("mechanic_family", optional(&item.mechanic_family)),
                    ("deterministic_part", optional(&item.deterministic_part)),
                    ("gm_part", optional(&item.gm_part)),
                ]));
            }
        }
        let (mut private_mechanics, mut procedures) = (Vec::new(), Vec::new());
        let runtime_bytes = store.package_file_bytes(
            entry.package_id(),
            entry.version(),
            entry.sha256(),
            PACKAGE_RUNTIME_PATH,
            PACKAGE_RUNTIME_BYTE_LIMIT as i64,
        )?;
        if let Some(bytes) = runtime_bytes {
            let public: BTreeSet<&str> = manifest.tools.iter().map(|tool| tool.name.as_str()).collect();
            (private_mechanics, procedures) = runtime_names(&bytes, &public)?;
        }
        // `manifest.metadata["label"]` and `manifest.provenance["publisher"]`: admission
        // requires both.
        let label = manifest.metadata.get("label").cloned().ok_or_else(|| StoreError::Other("'label'".into()))?;
        let publisher =
            manifest.provenance.get("publisher").cloned().ok_or_else(|| StoreError::Other("'publisher'".into()))?;
        packages.push(object(vec![
            ("id", entry.package_id().into()),
            ("version", entry.version().into()),
            ("content_sha256", entry.sha256().into()),
            ("role", entry.role().as_str().into()),
            ("load_order", entry.load_order().into()),
            ("label", label),
            ("publisher", publisher),
            ("provenance", Json::Object(manifest.provenance.clone())),
            ("compatibility", Json::Object(manifest.compatibility.clone())),
            ("archive_format", manifest.format_version.into()),
            (
                "dependencies",
                Json::Array(
                    manifest
                        .dependencies
                        .iter()
                        .map(|dependency| {
                            object(vec![
                                ("namespace", dependency.namespace().into()),
                                ("kind", dependency.kind().into()),
                                ("id", dependency.package_id().into()),
                                ("version", dependency.version().into()),
                                ("content_sha256", dependency.content_sha256().into()),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("capabilities", texts(manifest.capabilities.iter().map(String::as_str))),
            (
                "public_actions",
                Json::Array(
                    manifest
                        .tools
                        .iter()
                        .map(|tool| {
                            object(vec![
                                ("name", tool.name.clone().into()),
                                ("capability", tool.capability.clone().into()),
                                ("input_schema", Json::Object(tool.input_schema.clone())),
                                (
                                    "state_predicates",
                                    Json::Array(
                                        tool.state_predicates
                                            .iter()
                                            .map(|predicate| {
                                                object(vec![
                                                    ("namespace", predicate.namespace.clone().into()),
                                                    ("key", predicate.key.clone().into()),
                                                    ("equals", predicate.equals.clone()),
                                                    ("negated", predicate.negated.into()),
                                                ])
                                            })
                                            .collect(),
                                    ),
                                ),
                                ("turn_statuses", texts(tool.turn_statuses.iter().map(String::as_str))),
                                ("writers", texts(tool.writers.iter().map(String::as_str))),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("private_mechanics", texts(private_mechanics.iter().map(String::as_str))),
            ("procedures", texts(procedures.iter().map(String::as_str))),
            (
                "participant_artifacts",
                Json::Array(
                    manifest
                        .participant_artifacts
                        .iter()
                        .map(|artifact| {
                            object(vec![
                                ("kind", artifact.kind.clone().into()),
                                ("schema_id", artifact.schema_id.clone().into()),
                                ("schema_sha256", artifact.schema_sha256.clone().into()),
                                ("schema", Json::Object(artifact.schema.clone())),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("prompt_fragments", texts(manifest.prompt_fragments.iter().map(|item| item.identity.as_str()))),
            ("ui_surfaces", texts(manifest.ui_surfaces.iter().map(|item| item.identity.as_str()))),
            ("support_counts", Json::Object(support_counts)),
            ("support_records", Json::Array(support_records)),
            (
                "files",
                Json::Array(
                    manifest
                        .files
                        .iter()
                        .map(|item| {
                            object(vec![
                                ("path", item.path().into()),
                                ("bytes", item.byte_count().into()),
                                ("sha256", item.sha256().into()),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("artifact_links", Json::Array(Vec::new())),
        ]));
    }
    let Json::Object(payload) = object(vec![
        ("candidate_id", candidate.candidate_id().into()),
        ("status", candidate.status().as_str().into()),
        ("closure_sha256", candidate.closure_sha256().into()),
        ("packages", Json::Array(packages)),
    ]) else {
        unreachable!("object builds an object")
    };
    Ok(payload)
}

/// `AuthorityOwner.inspect_package_candidate(candidate_id)`: each locked package's exact
/// manifest facts, its runtime's private mechanics and procedures, its gameplay support claims
/// and its admitted files.
pub async fn inspect_package_candidate(store: &StoreHandle, candidate_id: &str) -> StoreResult<JsonMap> {
    let id = candidate_id.to_owned();
    store.call(move |store| inspect_on(store, &id)).await
}

/// `package_candidate_text`'s `maximum_bytes` default: 256 KiB.
pub const PACKAGE_TEXT_BYTE_LIMIT: i64 = 256 * 1024;

/// `AuthorityOwner.package_candidate_text(candidate_id, package_id, portable_path)`: one admitted
/// text file of a locked package. Only notices (`LICENSE`, `COPYRIGHT`) and `.yaml`, `.yml`,
/// `.json`, `.md` and `.txt` files are shown, checked before the owner's operation.
pub async fn package_candidate_text(
    store: &StoreHandle,
    candidate_id: &str,
    package_id: &str,
    portable_path: &str,
) -> StoreResult<JsonMap> {
    let notice_name = pystr::casefold(portable_path.rsplit('/').next().unwrap_or(portable_path));
    let folded = pystr::casefold(portable_path);
    if !matches!(notice_name.as_str(), "license" | "copyright")
        && ![".yaml", ".yml", ".json", ".md", ".txt"].iter().any(|suffix| folded.ends_with(suffix))
    {
        return Err(rejected_with("Installed package file is export-only", "package_preview_unsupported"));
    }
    let (id, package, path) = (candidate_id.to_owned(), package_id.to_owned(), portable_path.to_owned());
    let value = store
        .call(move |store| {
            let candidate = store.package_candidate(&id)?;
            let Some(entry) = candidate.package_lock().entries().iter().find(|item| item.package_id() == package)
            else {
                return Err(rejected_with("Installed package is unavailable", "package_unavailable"));
            };
            store.package_file_bytes(entry.package_id(), entry.version(), entry.sha256(), &path, PACKAGE_TEXT_BYTE_LIMIT)
        })
        .await?;
    let Some(value) = value else {
        return Err(rejected_with("Installed package file is unavailable", "package_file_unavailable"));
    };
    let Ok(content) = std::str::from_utf8(&value) else {
        return Err(rejected_with("Installed package file is not UTF-8", "package_preview_unsupported"));
    };
    let Json::Object(payload) = object(vec![
        ("candidate_id", candidate_id.into()),
        ("package_id", package_id.into()),
        ("path", portable_path.into()),
        ("bytes", (value.len() as i64).into()),
        ("sha256", sylvia_base::hash::sha256_hex(&value).into()),
        ("content", content.into()),
    ]) else {
        unreachable!("object builds an object")
    };
    Ok(payload)
}

/// `package_index(value)`: the inspection's packages by id, the last of a repeated id winning.
fn package_index(value: &JsonMap) -> StoreResult<Vec<(String, JsonMap)>> {
    let invalid = || rejected("Installed package comparison data is invalid");
    let Some(Json::Array(raw_packages)) = value.get("packages") else { return Err(invalid()) };
    let mut indexed: Vec<(String, JsonMap)> = Vec::new();
    for raw_package in raw_packages {
        let Json::Object(package) = raw_package else { return Err(invalid()) };
        let Some(Json::String(id)) = package.get("id") else { return Err(invalid()) };
        match indexed.iter_mut().find(|(key, _)| key == id) {
            Some(slot) => slot.1 = package.clone(),
            None => indexed.push((id.clone(), package.clone())),
        }
    }
    Ok(indexed)
}

/// `file_index(value)`: a package's file hashes by path.
fn file_index(value: &JsonMap) -> StoreResult<std::collections::BTreeMap<String, String>> {
    let invalid = || rejected("Installed package comparison data is invalid");
    let Some(Json::Array(raw_files)) = value.get("files") else { return Err(invalid()) };
    let mut indexed = std::collections::BTreeMap::new();
    for raw_file in raw_files {
        let Json::Object(file) = raw_file else { return Err(invalid()) };
        let (Some(Json::String(path)), Some(Json::String(sha256))) = (file.get("path"), file.get("sha256")) else {
            return Err(invalid());
        };
        indexed.insert(path.clone(), sha256.clone());
    }
    Ok(indexed)
}

/// `AuthorityOwner.compare_package_candidates(left_id, right_id)`: whether the two closures are
/// the same, and each package added, removed or changed (its versions, content hashes and the
/// files whose hashes differ), by package id.
pub async fn compare_package_candidates(store: &StoreHandle, left_id: &str, right_id: &str) -> StoreResult<JsonMap> {
    let left = inspect_package_candidate(store, left_id).await?;
    let right = inspect_package_candidate(store, right_id).await?;
    let left_packages = package_index(&left)?;
    let right_packages = package_index(&right)?;
    let find = |packages: &[(String, JsonMap)], id: &str| packages.iter().find(|(key, _)| key == id).map(|(_, p)| p.clone());
    let package_ids: BTreeSet<String> =
        left_packages.iter().chain(right_packages.iter()).map(|(id, _)| id.clone()).collect();
    let mut changes = Vec::new();
    for package_id in package_ids {
        let (left_package, right_package) = (find(&left_packages, &package_id), find(&right_packages, &package_id));
        let (Some(left_package), Some(right_package)) = (&left_package, &right_package) else {
            changes.push(object(vec![
                ("package_id", package_id.clone().into()),
                ("change", if left_package.is_none() { "added" } else { "removed" }.into()),
                ("changed_files", Json::Array(Vec::new())),
            ]));
            continue;
        };
        let left_files = file_index(left_package)?;
        let right_files = file_index(right_package)?;
        let paths: BTreeSet<&String> = left_files.keys().chain(right_files.keys()).collect();
        let changed_files: Vec<&str> = paths
            .into_iter()
            .filter(|path| left_files.get(*path) != right_files.get(*path))
            .map(String::as_str)
            .collect();
        let field = |package: &JsonMap, key: &str| package.get(key).cloned().unwrap_or(Json::Null);
        if field(left_package, "content_sha256") != field(right_package, "content_sha256") || !changed_files.is_empty() {
            changes.push(object(vec![
                ("package_id", package_id.clone().into()),
                ("change", "changed".into()),
                ("left_version", field(left_package, "version")),
                ("right_version", field(right_package, "version")),
                ("left_content_sha256", field(left_package, "content_sha256")),
                ("right_content_sha256", field(right_package, "content_sha256")),
                ("changed_files", texts(changed_files)),
            ]));
        }
    }
    let Json::Object(payload) = object(vec![
        ("left_candidate_id", left_id.into()),
        ("right_candidate_id", right_id.into()),
        ("same_closure", (left.get("closure_sha256") == right.get("closure_sha256")).into()),
        ("changes", Json::Array(changes)),
    ]) else {
        unreachable!("object builds an object")
    };
    Ok(payload)
}
