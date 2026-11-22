//! `application/plan_snapshot.py`: validate a Plan against the package bytes it carries, without
//! installing source content.
//!
//! [`admitted_snapshot_facts`] keeps Python's `lru_cache(maxsize=4)`: the successful facts of the
//! four most recently used snapshots, keyed by their exact bytes, process-wide.

use crate::bundled_packages::BundledPackages;
use crate::plan_network_admission::validate_plan_networks;
use crate::plan_spatial_admission::validate_plan_spatial_scenes;
use crate::plan_state_admission::validate_plan_state;
use std::collections::BTreeSet;
use std::sync::Mutex;
use sylvia_base::Json;
use sylvia_domain::authority::PackageRole;
use sylvia_domain::campaign_plan::{
    validate_campaign_plan, CampaignPlanExpectations, PackageBinding, ValidatedCampaignPlan,
};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{
    campaign_setting_entries, closure_sha256, parse_campaign_setting_knowledge_bytes, PackageCandidate,
    PackageManifest, CAMPAIGN_SETTING_KNOWLEDGE_PATH,
};
use sylvia_domain::pyops::py_eq;

/// `PLAN_SNAPSHOT_FILENAME`.
pub const PLAN_SNAPSHOT_FILENAME: &str = "setting.sylvia-gm-package";
/// `PLAN_SNAPSHOT_BYTE_LIMIT`.
pub const PLAN_SNAPSHOT_BYTE_LIMIT: usize = 64 * 1024 * 1024;

/// One Setting in a snapshot: its lock identity, file paths and knowledge identities.
#[derive(Debug, Clone, PartialEq)]
pub struct SnapshotSetting {
    pub binding: PackageBinding,
    pub paths: BTreeSet<String>,
    pub knowledge_ids: BTreeSet<String>,
}

/// `_admitted_snapshot_facts(package_snapshot)`: `(primary, settings, closure_sha256)`.
#[derive(Debug, Clone, PartialEq)]
pub struct SnapshotFacts {
    pub primary: PackageManifest,
    pub settings: Vec<SnapshotSetting>,
    pub closure_sha256: String,
}

/// The `lru_cache` entries, least recently used first.
static FACTS_CACHE: Mutex<Vec<(Vec<u8>, SnapshotFacts)>> = Mutex::new(Vec::new());
const FACTS_CACHE_SIZE: usize = 4;

/// `_admitted_snapshot_facts(package_snapshot)`: read exact bounded archive bytes once; retain no
/// handles or temporary files. Errors are not cached.
pub fn admitted_snapshot_facts(package_snapshot: &[u8]) -> DomainResult<SnapshotFacts> {
    {
        let mut cache = FACTS_CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Some(index) = cache.iter().position(|(bytes, _)| bytes.as_slice() == package_snapshot) {
            let entry = cache.remove(index);
            let facts = entry.1.clone();
            cache.push(entry);
            return Ok(facts);
        }
    }
    let facts = read_snapshot_facts(package_snapshot)?;
    let mut cache = FACTS_CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if !cache.iter().any(|(bytes, _)| bytes.as_slice() == package_snapshot) {
        cache.push((package_snapshot.to_vec(), facts.clone()));
        if cache.len() > FACTS_CACHE_SIZE {
            cache.remove(0);
        }
    }
    Ok(facts)
}

fn read_snapshot_facts(package_snapshot: &[u8]) -> DomainResult<SnapshotFacts> {
    let admitted = admit_package_archive(package_snapshot)?;
    let primary = admitted
        .manifests
        .iter()
        .find(|item| item.role == PackageRole::Primary)
        .ok_or_else(|| DomainError::uncaught("StopIteration", ""))?
        .clone();
    let mut settings = Vec::new();
    for setting in campaign_setting_entries(&admitted.package_lock) {
        let files: Vec<_> = admitted
            .files
            .iter()
            .filter(|item| admitted.manifests[item.manifest].package_id == setting.package_id())
            .collect();
        let paths = files.iter().map(|item| item.file.path().to_owned()).collect();
        let mut knowledge_ids = BTreeSet::new();
        if let Some(knowledge_file) =
            files.iter().find(|item| item.file.path() == CAMPAIGN_SETTING_KNOWLEDGE_PATH)
        {
            let knowledge = parse_campaign_setting_knowledge_bytes(&admitted.read_file(knowledge_file)?)?;
            knowledge_ids = knowledge.facts.into_iter().map(|item| item.identity).collect();
        }
        let binding = PackageBinding {
            package_id: setting.package_id().into(),
            version: setting.version().into(),
            content_sha256: setting.sha256().into(),
        };
        settings.push(SnapshotSetting { binding, paths, knowledge_ids });
    }
    Ok(SnapshotFacts { primary, settings, closure_sha256: closure_sha256(&admitted.package_lock) })
}

/// `payload.get(field)` when the payload is a mapping.
fn member<'a>(value: Option<&'a Json>, field: &str) -> Option<&'a Json> {
    value.and_then(Json::as_object).and_then(|map| map.get(field))
}

/// `validate_plan_snapshot(package_snapshot, payload, readable_markdown)`: require the exact
/// Setting, mechanics and referenced world files inside one saved copy.
pub fn validate_plan_snapshot(
    package_snapshot: &[u8],
    payload: &Json,
    readable_markdown: &str,
) -> DomainResult<ValidatedCampaignPlan> {
    if package_snapshot.is_empty() || package_snapshot.len() > PLAN_SNAPSHOT_BYTE_LIMIT {
        return Err(DomainError::value("Campaign Plan package snapshot is missing or too large"));
    }
    let setting_binding = member(member(Some(payload), "bindings"), "campaign_setting");
    let Some(Json::Object(setting_binding)) = setting_binding else {
        return Err(DomainError::value("Campaign Plan Setting binding is missing"));
    };
    let facts = admitted_snapshot_facts(package_snapshot)?;
    let wanted = setting_binding.get("id").unwrap_or(&Json::Null);
    let matching: Vec<_> = facts
        .settings
        .iter()
        .filter(|item| py_eq(&Json::from(item.binding.package_id.as_str()), wanted))
        .collect();
    let [setting] = matching.as_slice() else {
        return Err(DomainError::value("Campaign Plan snapshot must contain its exact Setting"));
    };
    let (package_id, version, content_sha256) = facts.primary.identity();
    let ruleset = PackageBinding {
        package_id: package_id.into(),
        version: version.into(),
        content_sha256: content_sha256.into(),
    };
    let validated = validate_campaign_plan(
        payload,
        readable_markdown,
        CampaignPlanExpectations {
            expected_ruleset: Some(&ruleset),
            expected_setting: Some(&setting.binding),
            expected_closure_sha256: &facts.closure_sha256,
            setting_paths: &setting.paths,
            setting_knowledge_ids: &setting.knowledge_ids,
        },
    )?;
    // Every Plan and every changed payload still undergoes all structural, state, spatial and
    // network checks.
    validate_plan_spatial_scenes(&validated.payload, &facts.primary.ui_surfaces)?;
    validate_plan_networks(&validated.payload, &facts.primary.ui_surfaces)?;
    validate_plan_state(&validated.payload, &facts.primary)?;
    Ok(validated)
}

/// The owner reads `retained_plan_snapshot` makes (`AuthorityOwner.package_candidates` and
/// `export_package_candidate`).
pub trait PlanSnapshotOwner {
    type Error: From<DomainError>;
    /// `owner.package_candidates()`.
    fn package_candidates(&self) -> Result<Vec<PackageCandidate>, Self::Error>;
    /// `owner.export_package_candidate(candidate_id, target)`: the written archive bytes.
    fn export_package_candidate(&self, candidate_id: &str) -> Result<Vec<u8>, Self::Error>;
}

/// `retained_plan_snapshot(owner, payload)`: capture the Plan's original packages; current
/// source versions do not replace them.
pub fn retained_plan_snapshot<O: PlanSnapshotOwner>(
    owner: &O,
    bundled: &BundledPackages,
    payload: &Json,
) -> Result<Vec<u8>, O::Error> {
    let expected = member(member(Some(payload), "bindings"), "package_closure_sha256");
    let expected_value = expected.unwrap_or(&Json::Null);
    let candidates = owner.package_candidates()?;
    let candidate = candidates.iter().find(|item| py_eq(&Json::from(item.closure_sha256()), expected_value));
    match candidate {
        Some(candidate) => owner.export_package_candidate(candidate.candidate_id()),
        None => match bundled.bundled_rift_archive_for_closure(expected_value)? {
            Some(snapshot) => Ok(snapshot),
            None => Err(DomainError::value("Campaign Plan retained packages are unavailable").into()),
        },
    }
}
