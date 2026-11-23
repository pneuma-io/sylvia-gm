//! `application/bundled_starter_characters.py`: the twelve authored SRD starters, admitted through
//! ordinary Character File storage.
//!
//! Python's process-wide `_ROSTERS` cache (under `_ROSTER_LOCK`) and the `lru_cache` of
//! `_bundled_bindings` are caches on [`BundledPackages`], keyed as Python keys them: the resolved
//! storage root, the exact package, schema and Setting identities, and the SHA-256 of the starter
//! source and of the Setting character schema.

use crate::bundled_packages::BundledPackages;
use crate::reusable_participants::{
    compatible_reusable_participants, save_reusable_participant, InvalidHandler, ReusableParticipant,
};
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::PackageLockEntry;
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::error::DomainResult;
use sylvia_domain::package_library::{canonical_json_bytes, ParticipantArtifactDeclaration};
use sylvia_domain::pyops::{py_eq, py_item, py_str};

const SRD_PACKAGE_ID: &str = "sylvia.srd-lite.bg3";
const SEVERED_LANDS_PACKAGE_ID: &str = "pneuma.severed-lands";
/// `if len(_ROSTERS) >= 32: _ROSTERS.clear()`.
const ROSTER_CACHE_LIMIT: usize = 32;

/// One exact `(ruleset id, version, sha256, setting id, version, sha256)` closure.
type Binding = [String; 6];
/// The `_ROSTERS` key.
type RosterKey = [String; 11];

/// The starter caches on [`BundledPackages`].
#[derive(Debug, Default)]
pub struct StarterCaches {
    bindings: std::sync::OnceLock<Vec<Binding>>,
    rosters: std::sync::Mutex<Vec<(RosterKey, Vec<ReusableParticipant>)>>,
}

fn binding_of(rules: &Json, setting: &Json) -> DomainResult<Binding> {
    let field = |value: &Json, key: &str| -> DomainResult<String> { Ok(py_str(py_item(value, key)?)) };
    Ok([
        field(rules, "id")?,
        field(rules, "version")?,
        field(rules, "content_sha256")?,
        field(setting, "id")?,
        field(setting, "version")?,
        field(setting, "content_sha256")?,
    ])
}

impl BundledPackages {
    /// `_bundled_bindings()`: offer starters only for exact shipped closures, never arbitrary
    /// same-ID Settings.
    fn starter_bindings(&self) -> DomainResult<&[Binding]> {
        if let Some(bindings) = self.starter_caches.bindings.get() {
            return Ok(bindings);
        }
        let authority = self.bundled_rift_authority()?;
        let mut pairs = vec![(
            Json::Object(authority.ruleset.as_payload()),
            Json::Object(authority.setting.as_payload()),
        )];
        for row in self.bundled_plan_catalog()? {
            pairs.push((row["ruleset"].clone(), row["campaign_setting"].clone()));
        }
        let mut bindings: Vec<Binding> = Vec::new();
        for (rules, setting) in &pairs {
            if !py_eq(py_item(rules, "id")?, &Json::from(SRD_PACKAGE_ID)) {
                continue;
            }
            let binding = binding_of(rules, setting)?;
            if !bindings.contains(&binding) {
                bindings.push(binding);
            }
        }
        Ok(self.starter_caches.bindings.get_or_init(|| bindings))
    }
}

/// `bundled_starter_participants(storage_root, package=..., declaration=..., campaign_setting=...,
/// setting_character_schema=...)`: concrete builds published against the selected bundled
/// declaration, through the ordinary immutable save path into `bundled-srd-starters`. The caller
/// gets its own copy of the cached roster.
pub fn bundled_starter_participants(
    storage_root: &Path,
    bundled: &BundledPackages,
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    campaign_setting: &PackageBinding,
    setting_character_schema: Option<&JsonMap>,
) -> DomainResult<Vec<ReusableParticipant>> {
    if declaration.kind != "player_character"
        || package.package_id() != SRD_PACKAGE_ID
        || campaign_setting.package_id != SEVERED_LANDS_PACKAGE_ID
    {
        return Ok(Vec::new());
    }
    let binding: Binding = [
        package.package_id().to_owned(),
        package.version().to_owned(),
        package.sha256().to_owned(),
        campaign_setting.package_id.clone(),
        campaign_setting.version.clone(),
        campaign_setting.content_sha256.clone(),
    ];
    if !bundled.starter_bindings()?.contains(&binding) {
        return Ok(Vec::new());
    }
    let source = crate::reusable_plans::read_text(&bundled.sources.srd_starters)?;
    let schema = setting_character_schema.cloned().unwrap_or_default();
    let key: RosterKey = [
        crate::reusable_library_deletion::resolve(storage_root),
        package.package_id().to_owned(),
        package.version().to_owned(),
        package.sha256().to_owned(),
        declaration.schema_id.clone(),
        declaration.schema_sha256.clone(),
        campaign_setting.package_id.clone(),
        campaign_setting.version.clone(),
        campaign_setting.content_sha256.clone(),
        sha256_hex(source.as_bytes()),
        sha256_hex(canonical_json_bytes(&Json::Object(schema))),
    ];
    // These first-party sources cannot be edited in the library. Avoid recalculating every build
    // on each Interview refresh, and never expose the cached objects to a caller.
    let mut rosters = bundled.starter_caches.rosters.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some((_, roster)) = rosters.iter().find(|(cached, _)| *cached == key) {
        return Ok(roster.clone());
    }
    let rows = crate::reusable_plans::loads(&source)?;
    let cache = storage_root.join("bundled-srd-starters");
    let mut roster = Vec::new();
    for row in sylvia_domain::pyops::py_iter(&rows)? {
        let operation_id = format!("bundled-srd-starter-{}", py_str(py_item(&row, "id")?));
        let mut saved = save_reusable_participant(
            &cache,
            &operation_id,
            "player_character",
            &py_str(py_item(&row, "summary")?),
            py_item(&row, "authored_profile")?,
            py_item(&row, "participant")?,
            py_item(&row, "entity_state")?,
            package,
            declaration,
            campaign_setting,
            None,
            setting_character_schema,
            None,
        )?;
        saved.builtin_starter = true;
        roster.push(saved);
    }
    if rosters.len() >= ROSTER_CACHE_LIMIT {
        rosters.clear();
    }
    rosters.push((key, roster.clone()));
    Ok(roster)
}

/// `available_reusable_participants(storage_root, package=..., declaration=...,
/// campaign_setting=..., legacy_campaign_setting=..., setting_character_schema=...,
/// on_invalid=...)`: the shipped roster, then the ordinary exact-bound saved characters.
#[allow(clippy::too_many_arguments)]
pub fn available_reusable_participants(
    storage_root: &Path,
    bundled: &BundledPackages,
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    campaign_setting: &PackageBinding,
    legacy_campaign_setting: Option<&PackageBinding>,
    setting_character_schema: Option<&JsonMap>,
    on_invalid: Option<InvalidHandler<'_>>,
) -> DomainResult<Vec<ReusableParticipant>> {
    let mut participants = bundled_starter_participants(
        storage_root,
        bundled,
        package,
        declaration,
        campaign_setting,
        setting_character_schema,
    )?;
    participants.extend(compatible_reusable_participants(
        storage_root,
        package,
        declaration,
        campaign_setting,
        legacy_campaign_setting,
        setting_character_schema,
        on_invalid,
    )?);
    Ok(participants)
}
