//! `application/bundled_campaign_plans.py`: selected built-in adventures and their exact
//! retained Setting snapshots.
//!
//! The module's `lru_cache`s are [`PlanCaches`] on [`BundledPackages`], which holds the bundled
//! content for the process. Like `lru_cache`, they keep only successful results. The collection
//! is read from [`crate::bundled_packages::BundledSources::campaign_plans_root`].

use crate::bundled_packages::BundledPackages;
use crate::package_materialization::{materialize_package_archive, NormalizedPackageDraft};
use crate::path_guardrail::platform;
use crate::plan_snapshot::{validate_plan_snapshot, PLAN_SNAPSHOT_FILENAME};
use crate::reusable_library_deletion::resolve;
use crate::reusable_plans::{
    load_reusable_campaign_plan, read_bytes, read_text, PlanValidator, ReusableCampaignPlan,
};
use serde_json::json;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use sylvia_base::{pyjson, pystr, Json, JsonMap};
use sylvia_domain::authority::PackageRole;
use sylvia_domain::campaign_plan::{parse_starting_party_policy, PackageBinding, ValidatedCampaignPlan};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::interview_contribution::parse_interview_contribution_bytes;
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_eq;

const SETTING_ID: &str = "pneuma.severed-lands";
const RIFT_ID: &str = "the-rift-awakening";
const DECLARATIONS_PATH: &str = "content/content-declarations.json";

/// `PlanSources`: one exact package snapshot shared by Plans with the same authored additions.
#[derive(Debug, Clone, PartialEq)]
pub struct PlanSources {
    pub snapshot: Vec<u8>,
    pub bindings: JsonMap,
    pub validated_plan: Option<ValidatedCampaignPlan>,
}

type Additions = Vec<(String, Vec<u8>)>;

/// The module's caches: `plan_sources` (8), `_catalogue` (1) and `_bundled_plan` (256).
#[derive(Debug, Default)]
pub struct PlanCaches {
    sources: Mutex<Vec<(Additions, PlanSources)>>,
    catalogue: Mutex<Option<Vec<JsonMap>>>,
    plans: Mutex<HashMap<String, Option<ReusableCampaignPlan>>>,
}

fn locked<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn json_bytes(data: &[u8]) -> DomainResult<Json> {
    pyjson::loads_bytes(data).map_err(|error| match error.error_type {
        Some(class @ ("JSONDecodeError" | "UnicodeDecodeError")) => {
            DomainError::Diagnosed { error_type: class, message: error.message, diagnostics: Vec::new() }
        }
        _ => DomainError::value(error.message),
    })
}

/// `json.loads(path.read_text(encoding="utf-8"))`.
fn read_json(path: &Path) -> DomainResult<Json> {
    crate::reusable_plans::loads(&read_text(path)?)
}

/// `_contained_plan_path(root, path)`: reject escaped paths before reading or selecting them.
fn contained_plan_path(root: &Path, path: PathBuf) -> DomainResult<PathBuf> {
    if platform::relative_parts(&resolve(&path), &resolve(root)).is_none() {
        return Err(DomainError::value("Bundled Plan file is missing or outside the selected collection"));
    }
    Ok(path)
}

/// `re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", text)`.
fn is_plan_identity(text: &str) -> bool {
    !text.is_empty()
        && text.split('-').all(|part| {
            !part.is_empty() && part.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
        })
}

/// `read_plan_catalogue(root)`: validate the explicit public inventory used by both packaging and
/// the chooser.
pub fn read_plan_catalogue(root: &Path) -> DomainResult<Vec<JsonMap>> {
    let catalogue = contained_plan_path(root, root.join("catalogue.json"))?;
    let Json::Array(rows) = read_json(&catalogue)? else {
        return Err(DomainError::value("Bundled Campaign Plan catalogue is invalid"));
    };
    if !(1..=256).contains(&rows.len()) {
        return Err(DomainError::value("Bundled Campaign Plan catalogue is invalid"));
    }
    let mut identities: Vec<String> = Vec::new();
    let mut result = Vec::new();
    for row in rows {
        let identity =
            row.as_object().and_then(|map| map.get("id")).and_then(Json::as_str).map(str::to_owned);
        let Some(identity) = identity.filter(|id| is_plan_identity(id) && !identities.contains(id)) else {
            return Err(DomainError::value("Bundled Campaign Plan identity is invalid or duplicated"));
        };
        let map = row.as_object().expect("checked above");
        let described = ["title", "pitch", "format"].iter().all(|key| {
            map.get(*key).and_then(Json::as_str).is_some_and(|text| !pystr::strip(text).is_empty())
        });
        if !described {
            return Err(DomainError::value("Bundled Campaign Plan public description is invalid"));
        }
        identities.push(identity);
        result.push(map.clone());
    }
    Ok(result)
}

/// `PurePosixPath(reference.path)` rejected as absolute, with `..`, or with a backslash.
fn invalid_character_path(path: &str) -> bool {
    path.starts_with('/') || path.split('/').any(|part| part == "..") || path.contains('\\')
}

/// `bundled_plan_files(root)`: package only selected Plans and their explicitly referenced
/// additional Character Files.
pub fn bundled_plan_files(root: &Path) -> DomainResult<Vec<PathBuf>> {
    let mut files = vec![root.join("catalogue.json")];
    let catalogue = read_plan_catalogue(root)?;
    for row in &catalogue {
        let directory = root.join(row["id"].as_str().expect("checked"));
        files.push(directory.join("plan.json"));
        files.push(directory.join("plan.md"));
        let snapshot = contained_plan_path(root, directory.join(PLAN_SNAPSHOT_FILENAME))?;
        if snapshot.is_file() {
            files.push(snapshot);
        }
        let contribution = contained_plan_path(root, directory.join("interview-contribution.json"))?;
        if contribution.is_file() {
            files.push(contribution);
        }
        let plan_path = contained_plan_path(root, directory.join("plan.json"))?;
        let plan = read_json(&plan_path)?;
        let party = parse_starting_party_policy(sylvia_domain::pyops::py_item(&plan, "starting_party")?)?;
        let mut references = party.required_companions.clone();
        references.extend(party.fixed_player_character.clone());
        for reference in references {
            if invalid_character_path(&reference.path) {
                return Err(DomainError::value("Bundled Plan character path is invalid"));
            }
            let addition = contained_plan_path(root, directory.join("setting-files").join(&reference.path))?;
            if addition.is_file() {
                files.push(addition);
            }
        }
    }
    let legacy = contained_plan_path(root, root.join(RIFT_ID))?;
    if !catalogue.iter().any(|row| row["id"] == RIFT_ID) && legacy.is_dir() {
        files.push(legacy.join("plan.json"));
        files.push(legacy.join("plan.md"));
        for name in [PLAN_SNAPSHOT_FILENAME, "interview-contribution.json"] {
            let retained = contained_plan_path(root, legacy.join(name))?;
            if retained.is_file() {
                files.push(retained);
            }
        }
    }
    for path in &files {
        contained_plan_path(root, path.clone())?;
        if !path.is_file() {
            return Err(DomainError::value(
                "Bundled Plan file is missing or outside the selected collection",
            ));
        }
    }
    Ok(files)
}

/// Every file under `base`, as `Path.rglob("*")` sorted, with POSIX relative names. Windows
/// orders paths case-insensitively, as `PureWindowsPath` compares them.
fn files_under(base: &Path) -> DomainResult<Vec<(String, PathBuf)>> {
    fn walk(directory: &Path, base: &Path, found: &mut Vec<(String, PathBuf)>) -> DomainResult<()> {
        let entries = match std::fs::read_dir(directory) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(crate::workspace_files::os_error(&error, directory)),
        };
        for entry in entries {
            let path = entry.map_err(|error| crate::workspace_files::os_error(&error, directory))?.path();
            if path.is_dir() {
                walk(&path, base, found)?;
            } else if path.is_file() {
                let relative = path.strip_prefix(base).expect("under the base");
                let name = relative
                    .components()
                    .map(|part| part.as_os_str().to_string_lossy())
                    .collect::<Vec<_>>()
                    .join("/");
                found.push((name, path));
            }
        }
        Ok(())
    }
    let mut found = Vec::new();
    walk(base, base, &mut found)?;
    let key = |name: &str| if cfg!(windows) { name.to_lowercase() } else { name.to_owned() };
    found.sort_by_key(|(name, _)| key(name));
    Ok(found)
}

fn binding_payload(identity: (&str, &str, &str)) -> Json {
    let (package_id, version, content_sha256) = identity;
    Json::Object(
        PackageBinding {
            package_id: package_id.into(),
            version: version.into(),
            content_sha256: content_sha256.into(),
        }
        .as_payload(),
    )
}

impl BundledPackages {
    /// `plan_sources(additions)`: keep Plan-owned Character Files in its contained Setting,
    /// never the base source.
    pub fn plan_sources(&self, additions: &[(String, Vec<u8>)]) -> DomainResult<PlanSources> {
        if let Some((_, sources)) = locked(&self.plan_caches.sources).iter().find(|(key, _)| key == additions)
        {
            return Ok(sources.clone());
        }
        let (rules, reference, mut setting) = self.rift_drafts()?;
        if !additions.is_empty() {
            let mut payloads = setting.payloads.clone();
            let index = payloads
                .iter()
                .position(|(name, _)| name == DECLARATIONS_PATH)
                .ok_or_else(|| DomainError::uncaught("KeyError", pystr::repr(DECLARATIONS_PATH)))?;
            let mut declarations = json_bytes(&payloads[index].1)?;
            let links: JsonMap = [
                "artifacts",
                "capabilities",
                "mechanics",
                "procedures",
                "prompt_fragments",
                "schemas",
                "state_namespaces",
                "tools",
                "ui_surfaces",
            ]
            .iter()
            .map(|key| ((*key).to_owned(), json!([])))
            .collect();
            let mut inclusion = json!({
                "id": "severed-lands-plan-characters",
                "kind": "campaign_setting",
                "family": "campaign-setting",
                "disposition": "included_nonmechanical",
                "classifications": ["sylvia_gm_implementation"],
                "sources": [],
                "links": links,
                "evidence": {"tests": ["severed-lands-campaign-collection"]},
            });
            for (path, data) in additions {
                match payloads.iter_mut().find(|(name, _)| name == path) {
                    Some((_, value)) => *value = data.clone(),
                    None => payloads.push((path.clone(), data.clone())),
                }
                inclusion["links"]["artifacts"]
                    .as_array_mut()
                    .expect("a list")
                    .push(Json::from(path.as_str()));
            }
            let inclusions = declarations
                .get_mut("inclusions")
                .and_then(Json::as_array_mut)
                .ok_or_else(|| DomainError::uncaught("KeyError", "'inclusions'"))?;
            inclusions.push(inclusion);
            let index = payloads.iter().position(|(name, _)| name == DECLARATIONS_PATH).expect("found above");
            payloads[index].1 = canonical_json_bytes(&declarations);
            setting = NormalizedPackageDraft::new(setting.record.clone(), payloads, 4);
        }
        let result = materialize_package_archive(&[rules, reference, setting])?;
        let primary = result.manifests.iter().find(|m| m.role == PackageRole::Primary).expect("a primary");
        let world = result.manifests.iter().find(|m| m.package_id == SETTING_ID).expect("the Setting");
        let mut bindings = JsonMap::new();
        bindings.insert("ruleset".into(), binding_payload(primary.identity()));
        bindings.insert("campaign_setting".into(), binding_payload(world.identity()));
        bindings.insert("package_closure_sha256".into(), Json::from(result.closure_sha256.as_str()));
        let sources = PlanSources { snapshot: result.archive, bindings, validated_plan: None };
        let mut cache = locked(&self.plan_caches.sources);
        cache.push((additions.to_vec(), sources.clone()));
        if cache.len() > 8 {
            cache.remove(0);
        }
        Ok(sources)
    }

    /// `sources_for(root)`: the retained portable snapshot, or this public Plan's authored
    /// Setting files.
    pub fn sources_for(&self, root: &Path) -> DomainResult<PlanSources> {
        let snapshot_path = root.join(PLAN_SNAPSHOT_FILENAME);
        if snapshot_path.is_file() {
            let snapshot = read_bytes(&snapshot_path)?;
            let plan = validate_plan_snapshot(
                &snapshot,
                &read_json(&root.join("plan.json"))?,
                &read_text(&root.join("plan.md"))?,
            )?;
            let bindings = plan.payload["bindings"].as_object().cloned().unwrap_or_default();
            return Ok(PlanSources { snapshot, bindings, validated_plan: Some(plan) });
        }
        let additions = files_under(&root.join("setting-files"))?
            .into_iter()
            .map(|(name, path)| {
                let data = read_bytes(&path)?;
                Ok((name, crlf_to_lf(&data)))
            })
            .collect::<DomainResult<Vec<_>>>()?;
        self.plan_sources(&additions)
    }

    /// `_catalogue()`.
    fn catalogue(&self) -> DomainResult<Vec<JsonMap>> {
        if let Some(rows) = locked(&self.plan_caches.catalogue).as_ref() {
            return Ok(rows.clone());
        }
        let rows = read_plan_catalogue(&self.sources.campaign_plans_root)?;
        *locked(&self.plan_caches.catalogue) = Some(rows.clone());
        Ok(rows)
    }

    /// `_bundled_plan(plan_id)`: validate one inventoried adventure against the snapshot shipped
    /// with it.
    fn load_bundled_plan(&self, plan_id: &str) -> DomainResult<Option<ReusableCampaignPlan>> {
        let plans_root = &self.sources.campaign_plans_root;
        let public = match self.catalogue()?.into_iter().find(|row| row["id"] == plan_id) {
            Some(public) => public,
            None => {
                // Retain exact access for older saved setup references without adding another
                // choice to the current public collection.
                if plan_id != RIFT_ID || !plans_root.join(plan_id).join("plan.json").is_file() {
                    return Ok(None);
                }
                let legacy = read_json(&plans_root.join(plan_id).join("plan.json"))?;
                let mut public = JsonMap::new();
                public.insert("id".into(), Json::from(plan_id));
                public.insert("title".into(), sylvia_domain::pyops::py_item(&legacy, "title")?.clone());
                public.insert("pitch".into(), Json::from("The original Rift Awakening campaign."));
                public
            }
        };
        let root = plans_root.join(plan_id);
        let sources = self.sources_for(&root)?;
        let plan = match sources.validated_plan.clone() {
            Some(plan) => plan,
            None => validate_plan_snapshot(
                &sources.snapshot,
                &read_json(&root.join("plan.json"))?,
                &read_text(&root.join("plan.md"))?,
            )?,
        };
        if !py_eq(&plan.payload["id"], &Json::from(plan_id))
            || !py_eq(&public["title"], &Json::from(plan.title.as_str()))
        {
            return Err(DomainError::value("Bundled Campaign Plan does not match its catalogue"));
        }
        let contribution_path = root.join("interview-contribution.json");
        let contribution = if contribution_path.is_file() {
            Some(
                parse_interview_contribution_bytes(&read_bytes(&contribution_path)?, "campaign_plan")?
                    .as_payload(),
            )
        } else {
            None
        };
        Ok(Some(ReusableCampaignPlan {
            artifact_id: plan_id.to_owned(),
            title: plan.title.clone(),
            summary: sylvia_domain::pyops::py_str(&public["pitch"]),
            content_sha256: plan.content_sha256.clone(),
            plan,
            interview_contribution: contribution,
            setup_id: (plan_id == RIFT_ID).then(|| RIFT_ID.to_owned()),
            package_snapshot: Some(sources.snapshot),
        }))
    }

    /// `bundled_plan(plan_id)`: caller-owned Plan data, so one request cannot alter later reads.
    pub fn bundled_plan(&self, plan_id: &str) -> DomainResult<Option<ReusableCampaignPlan>> {
        if let Some(plan) = locked(&self.plan_caches.plans).get(plan_id) {
            return Ok(plan.clone());
        }
        let plan = self.load_bundled_plan(plan_id)?;
        let mut cache = locked(&self.plan_caches.plans);
        if cache.len() >= 256 {
            cache.clear();
        }
        cache.insert(plan_id.to_owned(), plan.clone());
        Ok(plan)
    }

    /// `selected_campaign_plan(storage_root, plan_id, validate_plan=...)`: a built-in or saved
    /// selection, without copying built-ins into the user's library.
    pub fn selected_campaign_plan(
        &self,
        storage_root: &Path,
        plan_id: &str,
        validate_plan: Option<PlanValidator<'_>>,
    ) -> DomainResult<ReusableCampaignPlan> {
        match self.bundled_plan(plan_id)? {
            Some(plan) => Ok(plan),
            None => load_reusable_campaign_plan(storage_root, plan_id, validate_plan, self),
        }
    }

    /// `bundled_plan_catalog()`: public catalogue descriptions instead of private GM premises.
    pub fn bundled_plan_catalog(&self) -> DomainResult<Vec<JsonMap>> {
        let mut rows = Vec::new();
        for public in self.catalogue()? {
            let plan =
                self.bundled_plan(public["id"].as_str().expect("checked"))?.expect("a catalogued Plan");
            let mut row = plan.catalog_payload();
            row.shift_remove("reusable");
            row.insert("bundled".into(), Json::Bool(true));
            row.insert(
                "interview_contribution".into(),
                plan.interview_contribution.clone().map_or(Json::Null, Json::Object),
            );
            let browsing: JsonMap = ["format", "acts", "character_mode", "protagonist", "recommended_level"]
                .iter()
                .filter_map(|key| public.get(*key).map(|value| ((*key).to_owned(), value.clone())))
                .collect();
            row.insert("browsing".into(), Json::Object(browsing));
            rows.push(row);
        }
        Ok(rows)
    }

    /// `bundled_setting_snapshot(ruleset_id, setting_id)`: a world offered by the public bundled
    /// Plans, without inventing a binding.
    pub fn bundled_setting_snapshot(
        &self,
        ruleset_id: &str,
        setting_id: &str,
    ) -> DomainResult<Option<Vec<u8>>> {
        for row in self.bundled_plan_catalog()? {
            if py_eq(&row["ruleset"]["id"], &Json::from(ruleset_id))
                && py_eq(&row["campaign_setting"]["id"], &Json::from(setting_id))
            {
                if let Some(selected) = self.bundled_plan(row["id"].as_str().expect("an id"))? {
                    return Ok(selected.package_snapshot);
                }
            }
        }
        Ok(None)
    }

    /// `known_builtin_public_summary(plan)`: the public hook for exact retained story copies,
    /// without rewriting their pins. Bindings may differ; every other field and the complete GM
    /// Markdown must match.
    pub fn known_builtin_public_summary(&self, plan: &ValidatedCampaignPlan) -> DomainResult<Option<String>> {
        let id = plan.payload.get("id").map(sylvia_domain::pyops::py_str).unwrap_or_default();
        let Some(known) = self.bundled_plan(&id)? else { return Ok(None) };
        if known.plan.readable_markdown != plan.readable_markdown {
            return Ok(None);
        }
        let without_bindings = |payload: &JsonMap| -> Json {
            Json::Object(
                payload
                    .iter()
                    .filter(|(key, _)| *key != "bindings")
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect(),
            )
        };
        if canonical_json_bytes(&without_bindings(&known.plan.payload))
            != canonical_json_bytes(&without_bindings(&plan.payload))
        {
            return Ok(None);
        }
        Ok(Some(known.summary))
    }
}

/// `bytes.replace(b"\r\n", b"\n")`.
fn crlf_to_lf(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len());
    let mut index = 0;
    while index < data.len() {
        if data[index] == b'\r' && data.get(index + 1) == Some(&b'\n') {
            index += 1;
            continue;
        }
        out.push(data[index]);
        index += 1;
    }
    out
}
