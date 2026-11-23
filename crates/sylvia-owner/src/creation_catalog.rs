//! `interfaces/owner_process.py`'s `_campaign_creation_catalog`: the exact content a new
//! campaign can be made from (bundled, installed and saved), with `_package_version_priority`,
//! `_preferred_campaign_candidates`, `_unavailable_library_content`,
//! `_reusable_participant_catalog`, `_rift_participant_authority` and
//! `_rift_reusable_participants`. Lane 5.
//!
//! The catalog reads the store directly, as Python's owner does. A store failure is read back
//! as the domain error Python would have raised (lane 1's `OwnerError::as_domain`), so the
//! library errors Python catches (`OSError`, `ValueError` and its subclasses) are caught here
//! too.

use indexmap::IndexMap;
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use sylvia_app::builder_references::OwnerError;
use sylvia_app::bundled_packages::BundledPackages;
use sylvia_app::bundled_starter_characters::available_reusable_participants;
use sylvia_app::campaign_library_selection::participant_catalog_payload;
use sylvia_app::campaign_plan_sources::{campaign_plan_sources, validate_retained_campaign_plan};
use sylvia_app::character_profiles::character_profile_catalog;
use sylvia_app::interview_content::ARCHIVED_RULESET_IDS;
use sylvia_app::reusable_participants::{compatible_reusable_participants, ReusableParticipant};
use sylvia_app::reusable_plans::reusable_campaign_plan_catalog;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, PackageRole};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::package_library::{campaign_setting_entries, CandidateStatus, PackageCandidate};
use sylvia_domain::pyops::{py_eq, py_str, py_truthy};
use sylvia_domain::{DomainError, DomainResult};
use sylvia_store::{AuthorityStore, StoreError, StoreResult};

/// The library errors Python's `except (OSError, ValueError)` (and its
/// `UnicodeError`/`JSONDecodeError` spellings) catches; `CommandRejected` is a `RuntimeError`.
fn caught(error: &DomainError) -> bool {
    error.is_value_error()
        || matches!(
            error.python_class(),
            "OSError"
                | "FileNotFoundError"
                | "FileExistsError"
                | "PermissionError"
                | "IsADirectoryError"
                | "NotADirectoryError"
                | "UnicodeError"
                | "UnicodeDecodeError"
                | "UnicodeEncodeError"
                | "JSONDecodeError"
        )
}

fn store(error: StoreError) -> DomainError {
    error.as_domain()
}

fn text(value: &str) -> Json {
    Json::String(value.to_owned())
}

fn is(item: &JsonMap, key: &str, value: &str) -> bool {
    item.get(key).and_then(Json::as_str) == Some(value)
}

/// A whole number as Python's `int(text)` holds it: compared by size, then digits.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Number(String);

impl Number {
    /// `int(digits)`, for the decimal digits of any script that `\d` matches.
    fn parse(digits: &str) -> Self {
        let ascii: String = digits
            .chars()
            .map(|c| pystr::decimal(c).and_then(|value| char::from_digit(value, 10)).unwrap_or('0'))
            .collect();
        let trimmed = ascii.trim_start_matches('0');
        Self(if trimmed.is_empty() { "0".into() } else { trimmed.into() })
    }
}

impl Ord for Number {
    fn cmp(&self, other: &Self) -> Ordering {
        self.0.len().cmp(&other.0.len()).then_with(|| self.0.cmp(&other.0))
    }
}

impl PartialOrd for Number {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// `_package_version_priority(version)`'s tuple.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct Priority(u8, Number, Number, Number, u8, Vec<(u8, String)>, String);

/// `_SEMANTIC_VERSION.fullmatch(version)`: `^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$`.
fn semantic(version: &str) -> Option<(Vec<&str>, Option<&str>)> {
    let identifier = |text: &str| {
        !text.is_empty() && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
    };
    let (rest, build) = match version.split_once('+') {
        Some((rest, build)) => (rest, Some(build)),
        None => (version, None),
    };
    if build.is_some_and(|build| !identifier(build)) {
        return None;
    }
    // The prerelease starts at the first `-` after the numbers (a number has no `-`).
    let (numbers, prerelease) = match rest.split_once('-') {
        Some((numbers, prerelease)) => (numbers, Some(prerelease)),
        None => (rest, None),
    };
    if prerelease.is_some_and(|prerelease| !identifier(prerelease)) {
        return None;
    }
    let parts: Vec<&str> = numbers.split('.').collect();
    // Python's `\d` matches a decimal digit of any script (category Nd), as `int()` reads it; the
    // prerelease and build identifiers are ASCII.
    if parts.len() > 3
        || parts.iter().any(|part| part.is_empty() || !part.chars().all(|c| pystr::decimal(c).is_some()))
    {
        return None;
    }
    Some((parts, prerelease))
}

/// `_package_version_priority(version)`: ordinary semantic versions in order, with a
/// deterministic fallback for anything else.
fn priority(version: &str) -> Priority {
    let folded = pystr::casefold(version);
    let Some((parts, prerelease)) = semantic(version) else {
        return Priority(
            0,
            Number::parse("0"),
            Number::parse("0"),
            Number::parse("0"),
            0,
            Vec::new(),
            folded,
        );
    };
    let number = |index: usize| Number::parse(parts.get(index).copied().unwrap_or("0"));
    let key = prerelease
        .map(|prerelease| {
            prerelease
                .split('.')
                .map(|part| {
                    if !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit()) {
                        (0, format!("{:0>20}", Number::parse(part).0))
                    } else {
                        (1, pystr::casefold(part))
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    Priority(1, number(0), number(1), number(2), u8::from(prerelease.is_none()), key, folded)
}

fn primary(candidate: &PackageCandidate) -> Option<&PackageLockEntry> {
    candidate.package_lock().entries().iter().find(|entry| entry.role() == PackageRole::Primary)
}

/// `_preferred_campaign_candidates(candidates)`: one campaign candidate per Ruleset, by version.
fn preferred_campaign_candidates(candidates: &[PackageCandidate]) -> Vec<&PackageCandidate> {
    let mut preferred: IndexMap<String, (&PackageCandidate, String)> = IndexMap::new();
    for candidate in candidates {
        if !matches!(candidate.status(), CandidateStatus::Validated | CandidateStatus::Activated) {
            continue;
        }
        let Some(primary) = primary(candidate) else { continue };
        let better = preferred
            .get(primary.package_id())
            .is_none_or(|current| priority(primary.version()) > priority(&current.1));
        if better {
            preferred.insert(primary.package_id().to_owned(), (candidate, primary.version().to_owned()));
        }
    }
    preferred.into_values().map(|(candidate, _)| candidate).collect()
}

/// `_unavailable_library_content(kind, artifact_id, error)`: a rejected saved item, without
/// malformed payloads or parser text.
fn unavailable_library_content(kind: &str, artifact_id: &str, error: &DomainError) -> JsonMap {
    let label = if kind == "campaign_plan" { "Saved Campaign Plan" } else { "Saved character" };
    let mut item = JsonMap::new();
    item.insert("kind".into(), text("unavailable_content"));
    item.insert("content_kind".into(), text(kind));
    item.insert("id".into(), text(artifact_id));
    item.insert("title".into(), Json::String(format!("{label} unavailable")));
    item.insert(
        "summary".into(),
        text("The saved files do not meet their content requirements. Repair a copy in Builder."),
    );
    item.insert(
        "validation_errors".into(),
        Json::Array(
            error
                .diagnostics()
                .iter()
                .take(8)
                .map(|diagnostic| Json::Object(diagnostic.as_payload()))
                .collect(),
        ),
    );
    item
}

type Unavailable = IndexMap<(String, String), JsonMap>;

fn record_invalid(unavailable: &mut Unavailable, kind: &str, artifact_id: &str, error: &DomainError) {
    unavailable.insert(
        (kind.to_owned(), artifact_id.to_owned()),
        unavailable_library_content(kind, artifact_id, error),
    );
}

/// `_rift_participant_authority()` and `_rift_reusable_participants(...)`.
fn rift_reusable_participants(
    storage_root: &Path,
    bundled: &BundledPackages,
    on_invalid: &mut dyn FnMut(&str, &DomainError),
    include_starters: bool,
) -> DomainResult<Vec<ReusableParticipant>> {
    let authority = bundled.bundled_rift_authority()?;
    let binding = &authority.ruleset;
    let package = PackageLockEntry::new(
        binding.package_id.clone(),
        binding.version.clone(),
        binding.content_sha256.clone(),
        PackageRole::Primary,
        0,
        Vec::new(),
        Vec::new(),
    )?;
    let declaration = bundled.bundled_rift_participant_declaration()?;
    let setting = authority.setting.clone();
    if include_starters {
        available_reusable_participants(
            storage_root,
            bundled,
            &package,
            &declaration,
            &setting,
            Some(&setting),
            None,
            Some(on_invalid),
        )
    } else {
        compatible_reusable_participants(
            storage_root,
            &package,
            &declaration,
            &setting,
            Some(&setting),
            None,
            Some(on_invalid),
        )
    }
}

/// `_reusable_participant_catalog(storage_root, owner, candidates=..., on_invalid=...,
/// include_starters=...)`: saved characters validated even when setup defers built-in roster
/// construction.
fn reusable_participant_catalog(
    storage_root: &Path,
    store_owner: &AuthorityStore,
    bundled: &BundledPackages,
    candidates: &[PackageCandidate],
    on_invalid: &mut dyn FnMut(&str, &DomainError),
    include_starters: bool,
) -> DomainResult<Vec<ReusableParticipant>> {
    if !include_starters {
        let library_root = storage_root.join("participants");
        let empty = std::fs::read_dir(&library_root).map_or(true, |mut entries| entries.next().is_none());
        if !library_root.exists() || empty {
            return Ok(Vec::new());
        }
    }
    let mut found: BTreeMap<String, ReusableParticipant> = BTreeMap::new();
    for item in rift_reusable_participants(storage_root, bundled, on_invalid, include_starters)? {
        found.insert(item.artifact_id.clone(), item);
    }
    let mut inspected: BTreeSet<(String, String, String, String, String)> = BTreeSet::new();
    for candidate in candidates {
        let Some(primary) = primary(candidate) else { continue };
        let settings = campaign_setting_entries(candidate.package_lock());
        let declarations = store_owner
            .exact_participant_declarations(primary.package_id(), primary.version(), primary.sha256())
            .map_err(store)?;
        for declaration in &declarations {
            for setting in &settings {
                let key = (
                    primary.sha256().to_owned(),
                    declaration.schema_id.clone(),
                    declaration.schema_sha256.clone(),
                    setting.package_id().to_owned(),
                    setting.sha256().to_owned(),
                );
                if !inspected.insert(key) {
                    continue;
                }
                let sources =
                    campaign_plan_sources(store_owner, candidate.package_lock(), Some(setting.package_id()))
                        .map_err(store)?;
                let Some(sources) = sources else { continue };
                let binding = PackageBinding {
                    package_id: setting.package_id().to_owned(),
                    version: setting.version().to_owned(),
                    content_sha256: setting.sha256().to_owned(),
                };
                let schema = sources.setting_character_schema.as_ref();
                let discovered = if include_starters {
                    available_reusable_participants(
                        storage_root,
                        bundled,
                        primary,
                        declaration,
                        &binding,
                        None,
                        schema,
                        Some(&mut *on_invalid),
                    )?
                } else {
                    compatible_reusable_participants(
                        storage_root,
                        primary,
                        declaration,
                        &binding,
                        None,
                        schema,
                        Some(&mut *on_invalid),
                    )?
                };
                for item in discovered {
                    found.insert(item.artifact_id.clone(), item);
                }
            }
        }
    }
    Ok(found.into_values().collect())
}

/// `tuple(item.get(key, ()))` as a list of the item's bindings.
fn listed(item: &JsonMap, key: &str) -> Vec<Json> {
    match item.get(key) {
        Some(Json::Array(values)) => values.clone(),
        _ => Vec::new(),
    }
}

fn binding_with(base: &JsonMap, ruleset: &JsonMap) -> Json {
    let mut binding = base.clone();
    binding.insert("candidate_id".into(), Json::Null);
    binding.insert("ruleset".into(), Json::Object(ruleset.clone()));
    Json::Object(binding)
}

fn object(value: &Json) -> DomainResult<JsonMap> {
    value.as_object().cloned().ok_or_else(|| DomainError::uncaught("TypeError", "expected a mapping"))
}

fn field<'a>(item: &'a JsonMap, key: &str) -> DomainResult<&'a Json> {
    item.get(key).ok_or_else(|| DomainError::uncaught("KeyError", format!("'{key}'")))
}

/// `_campaign_creation_catalog(storage_root, application_owner, include_participants=...)`:
/// exact content; setup can defer character construction until party selection.
pub fn campaign_creation_catalog(
    storage_root: &Path,
    store_owner: &AuthorityStore,
    bundled: &BundledPackages,
    include_participants: bool,
) -> StoreResult<Vec<JsonMap>> {
    catalog(storage_root, store_owner, bundled, include_participants).map_err(StoreError::from)
}

/// `if application_owner.legacy_builder_contexts(): migrate_legacy_builder_chats(owner,
/// storage_root, _campaign_creation_catalog(storage_root, owner))`. Profiles without such chats
/// start as before; the read creates no session tables in a fresh database.
pub fn migrate_legacy_builder_chats(
    store: &AuthorityStore,
    bundled: &BundledPackages,
    storage_root: &Path,
) -> StoreResult<Vec<String>> {
    if store.legacy_builder_contexts()?.is_empty() {
        return Ok(Vec::new());
    }
    let catalog = campaign_creation_catalog(storage_root, store, bundled, true)?;
    sylvia_app::builder_session_migration::migrate_legacy_builder_chats(
        store,
        bundled,
        storage_root,
        &catalog,
    )
}

fn catalog(
    storage_root: &Path,
    store_owner: &AuthorityStore,
    bundled: &BundledPackages,
    include_participants: bool,
) -> DomainResult<Vec<JsonMap>> {
    let mut items: Vec<JsonMap> = bundled.bundled_campaign_content_catalog()?;
    let candidates: Vec<PackageCandidate> = store_owner
        .package_candidates()
        .map_err(store)?
        .into_iter()
        .filter(|candidate| {
            !candidate.package_lock().entries().iter().any(|entry| {
                entry.role() == PackageRole::Primary && ARCHIVED_RULESET_IDS.contains(&entry.package_id())
            })
        })
        .collect();
    let mut unavailable: Unavailable = IndexMap::new();

    let mut on_profile = |identity: &str, error: &DomainError| {
        record_invalid(&mut unavailable, "character_profile", identity, error);
    };
    match character_profile_catalog(storage_root, &mut on_profile) {
        Ok(profiles) => {
            for profile in profiles {
                items.push(object(&profile.catalog_payload())?);
            }
        }
        Err(error) if caught(&error) => {
            record_invalid(&mut unavailable, "character_profile", "character-profile-library", &error)
        }
        Err(error) => return Err(error),
    }

    let validate = |payload: &Json, markdown: &str| {
        validate_retained_campaign_plan(store_owner, bundled, payload, markdown, Some(&candidates))
            .map_err(store)
    };
    let plans = {
        let mut on_plan = |artifact_id: &str, error: &DomainError| {
            record_invalid(&mut unavailable, "campaign_plan", artifact_id, error);
        };
        reusable_campaign_plan_catalog(storage_root, Some(&validate), Some(&mut on_plan), bundled)
    };
    let invalid_plans = || {
        DomainError::command_rejected_with(
            "Reusable Campaign Plan library contains an invalid artifact",
            "reusable_campaign_plan_invalid",
            Vec::new(),
        )
    };
    match plans {
        Ok(plans) => {
            for retained in plans {
                let mut projected = retained.catalog_payload();
                match bundled.known_builtin_public_summary(&retained.plan) {
                    Ok(Some(summary)) => {
                        projected.insert("summary".into(), Json::String(summary));
                    }
                    Ok(None) => {}
                    Err(error) if caught(&error) => return Err(invalid_plans()),
                    Err(error) => return Err(error),
                }
                items.push(projected);
            }
        }
        Err(error) if caught(&error) => return Err(invalid_plans()),
        Err(error) => return Err(error),
    }

    let participants = {
        let mut on_participant = |artifact_id: &str, error: &DomainError| {
            record_invalid(&mut unavailable, "participant", artifact_id, error);
        };
        reusable_participant_catalog(
            storage_root,
            store_owner,
            bundled,
            &candidates,
            &mut on_participant,
            include_participants,
        )
        .and_then(|participants| {
            let mut rows = Vec::new();
            if include_participants {
                for participant in &participants {
                    rows.push(participant_catalog_payload(store_owner, participant).map_err(store)?);
                }
            }
            Ok(rows)
        })
    };
    match participants {
        Ok(rows) => items.extend(rows),
        Err(error) if caught(&error) => {
            return Err(DomainError::command_rejected_with(
                "Reusable participant library contains an invalid artifact",
                "reusable_participant_invalid",
                Vec::new(),
            ))
        }
        Err(error) => return Err(error),
    }

    // Portable built-in Plans own exact world snapshots. Offer those worlds before
    // installation; the Interview admits the selected snapshot through normal checks.
    let public_plans: Vec<JsonMap> = items
        .iter()
        .filter(|item| {
            is(item, "kind", "campaign_plan")
                && item.get("ruleset").is_some_and(py_truthy)
                && item.get("campaign_setting").is_some_and(py_truthy)
        })
        .cloned()
        .collect();
    for plan_item in &public_plans {
        let rule = object(field(plan_item, "ruleset")?)?;
        let world = object(field(plan_item, "campaign_setting")?)?;
        let rule_id = field(&rule, "id")?.clone();
        let position = items.iter().position(|item| {
            is(item, "kind", "ruleset") && item.get("id").is_some_and(|id| py_eq(id, &rule_id))
        });
        let index = match position {
            Some(index) => index,
            None => {
                let mut choice = JsonMap::new();
                choice.insert("kind".into(), text("ruleset"));
                choice.extend(rule.clone());
                choice.insert("title".into(), rule_id.clone());
                choice.insert("selection_id".into(), rule_id.clone());
                choice.insert("compatible_settings".into(), Json::Array(Vec::new()));
                let identity =
                    (field(&rule, "id")?, field(&rule, "version")?, field(&rule, "content_sha256")?);
                let installed = candidates
                    .iter()
                    .flat_map(|candidate| candidate.package_lock().entries())
                    .find(|entry| {
                        let (id, version, sha256) = entry.identity();
                        py_eq(identity.0, &text(id))
                            && py_eq(identity.1, &text(version))
                            && py_eq(identity.2, &text(sha256))
                    });
                if let Some(installed) = installed {
                    let label = store_owner
                        .package_label(installed.package_id(), installed.version(), installed.sha256())
                        .map_err(store)?;
                    choice.insert("title".into(), Json::String(label));
                }
                items.push(choice);
                items.len() - 1
            }
        };
        let choice = &mut items[index];
        let mut settings = listed(choice, "compatible_settings");
        let world_id = field(&world, "id")?.clone();
        let present = settings.iter().any(|binding| binding.get("id").is_some_and(|id| py_eq(id, &world_id)));
        if !present {
            settings.push(binding_with(&world, &rule));
            choice.insert("compatible_settings".into(), Json::Array(settings));
        }
    }

    // A catalogue may replace the historical starter with portable Plans. The legacy binding
    // adjustment is needed only when that Plan is inventoried.
    let authority = bundled.bundled_rift_authority()?;
    let mut bundled_ruleset = authority.ruleset.as_payload();
    let mut bundled_setting = authority.setting.as_payload();
    if let Some(plan) = items
        .iter()
        .find(|item| is(item, "kind", "campaign_plan") && is(item, "setup_id", "the-rift-awakening"))
    {
        bundled_ruleset = object(field(plan, "ruleset")?)?;
        bundled_setting = object(field(plan, "campaign_setting")?)?;
    }
    let ruleset_id = field(&bundled_ruleset, "id")?.clone();
    let setting_id = field(&bundled_setting, "id")?.clone();
    for item in &mut items {
        let matches = |item: &JsonMap, id: &Json| item.get("id").is_some_and(|value| py_eq(value, id));
        if is(item, "kind", "ruleset") && matches(item, &ruleset_id) {
            item.insert("content_sha256".into(), field(&bundled_ruleset, "content_sha256")?.clone());
            let id = field(item, "id")?.clone();
            item.insert("selection_id".into(), id);
            let mut settings: Vec<Json> = listed(item, "compatible_settings")
                .into_iter()
                .filter(|existing| !existing.get("id").is_some_and(|id| py_eq(id, &setting_id)))
                .collect();
            settings.push(binding_with(&bundled_setting, &bundled_ruleset));
            item.insert("compatible_settings".into(), Json::Array(settings));
        } else if is(item, "kind", "campaign_setting") && matches(item, &setting_id) {
            item.insert("content_sha256".into(), field(&bundled_setting, "content_sha256")?.clone());
        }
    }

    let mut ruleset_indexes: BTreeMap<String, usize> = BTreeMap::new();
    for (index, item) in items.iter().enumerate() {
        if is(item, "kind", "ruleset") {
            ruleset_indexes.insert(py_str(field(item, "id")?), index);
        }
    }
    let identity_of = |item: &JsonMap| -> DomainResult<(String, String, String)> {
        Ok((
            py_str(field(item, "id")?),
            py_str(field(item, "version")?),
            py_str(item.get("content_sha256").unwrap_or(&text(""))),
        ))
    };
    let mut known_settings: BTreeSet<(String, String, String)> = BTreeSet::new();
    for item in &items {
        if is(item, "kind", "campaign_setting") {
            known_settings.insert(identity_of(item)?);
        }
    }
    let bundled_identity = (
        field(&bundled_ruleset, "id")?.clone(),
        field(&bundled_ruleset, "version")?.clone(),
        field(&bundled_ruleset, "content_sha256")?.clone(),
    );
    let label =
        |id: &str, version: &str, sha256: &str| store_owner.package_label(id, version, sha256).map_err(store);
    for candidate in preferred_campaign_candidates(&candidates) {
        let Some(primary) = primary(candidate) else { continue };
        let existing_index = ruleset_indexes.get(primary.package_id()).copied();
        let candidate_priority = priority(primary.version());
        let existing_priority =
            existing_index.map(|index| priority(&py_str(items[index].get("version").unwrap_or(&text("0")))));
        if existing_priority.as_ref().is_some_and(|existing| candidate_priority < *existing) {
            continue;
        }
        let setting_identities: Vec<(String, String, String)> =
            campaign_setting_entries(candidate.package_lock())
                .into_iter()
                .map(|entry| {
                    (entry.package_id().to_owned(), entry.version().to_owned(), entry.sha256().to_owned())
                })
                .collect();
        if setting_identities.is_empty()
            && existing_priority.as_ref().is_some_and(|existing| candidate_priority <= *existing)
        {
            continue;
        }
        if let (Some(index), Some(existing)) = (existing_index, existing_priority.as_ref()) {
            if candidate_priority == *existing {
                let existing_item = &items[index];
                let existing_sha = py_str(existing_item.get("content_sha256").unwrap_or(&text("")));
                let existing_settings: BTreeSet<(String, String, String)> =
                    listed(existing_item, "compatible_settings")
                        .iter()
                        .filter_map(Json::as_object)
                        .map(|binding| {
                            let part = |key: &str| py_str(binding.get(key).unwrap_or(&text("")));
                            (part("id"), part("version"), part("content_sha256"))
                        })
                        .collect();
                let offered: BTreeSet<(String, String, String)> =
                    setting_identities.iter().cloned().collect();
                let primary_conflict = !existing_sha.is_empty() && primary.sha256() != existing_sha;
                let dependency_conflict =
                    !existing_settings.is_empty() && !existing_settings.is_subset(&offered);
                if primary_conflict || dependency_conflict {
                    // Package versions are immutable identities: persisted equal-version bytes
                    // that no longer match the bundle cannot replace it for a new Draft.
                    continue;
                }
            }
        }
        let mut setting_bindings = Vec::new();
        for identity in &setting_identities {
            setting_bindings.push(serde_json::json!({
                "id": identity.0, "version": identity.1, "content_sha256": identity.2,
                "candidate_id": candidate.candidate_id(),
            }));
            if !known_settings.contains(identity) {
                items.push(setting_item(identity, label(&identity.0, &identity.1, &identity.2)?));
                known_settings.insert(identity.clone());
            }
        }
        let title = match existing_index {
            Some(index) => {
                let existing = items[index].get("title").filter(|title| py_truthy(title));
                existing.map_or_else(|| primary.package_id().to_owned(), py_str)
            }
            None => label(primary.package_id(), primary.version(), primary.sha256())?,
        };
        let is_bundled = py_eq(&bundled_identity.0, &text(primary.package_id()))
            && py_eq(&bundled_identity.1, &text(primary.version()))
            && py_eq(&bundled_identity.2, &text(primary.sha256()));
        let kept_selection = existing_index.is_some_and(|index| {
            is(&items[index], "selection_id", primary.package_id())
                && is(&items[index], "content_sha256", primary.sha256())
        });
        let selection_id =
            if is_bundled || kept_selection { primary.package_id() } else { candidate.candidate_id() };
        let aliases: Vec<Json> = candidates
            .iter()
            .filter(|item| {
                item.package_lock().entries().iter().any(|entry| {
                    entry.role() == PackageRole::Primary && entry.identity() == primary.identity()
                })
            })
            .map(|item| text(item.candidate_id()))
            .collect();
        let mut choice = JsonMap::new();
        choice.insert("kind".into(), text("ruleset"));
        choice.insert("id".into(), text(primary.package_id()));
        choice.insert("version".into(), text(primary.version()));
        choice.insert("content_sha256".into(), text(primary.sha256()));
        choice.insert("title".into(), Json::String(title));
        choice.insert("selection_id".into(), text(selection_id));
        choice.insert("compatible_settings".into(), Json::Array(setting_bindings));
        choice.insert("selection_aliases".into(), Json::Array(aliases));
        match existing_index {
            None => {
                ruleset_indexes.insert(primary.package_id().to_owned(), items.len());
                items.push(choice);
            }
            Some(index) => items[index] = choice,
        }
    }

    // A Setting owns its Ruleset copy. Discover every such Setting independently of which
    // source Ruleset version is currently preferred in the first picker.
    let inherited: BTreeSet<String> = candidates
        .iter()
        .filter(|candidate| {
            candidate.package_lock().entries().iter().any(|member| {
                member.dependencies().iter().any(|dependency| dependency.kind() == "ruleset_snapshot")
            })
        })
        .flat_map(|candidate| candidate.package_lock().entries().iter())
        .filter(|entry| entry.role() == PackageRole::Primary)
        .map(|entry| entry.package_id().to_owned())
        .collect();
    let bundled_setting_version = py_str(field(&bundled_setting, "version")?);
    let bundled_setting_sha = field(&bundled_setting, "content_sha256")?.clone();
    let mut snapshots: IndexMap<String, IndexMap<String, Json>> = IndexMap::new();
    for candidate in &candidates {
        let primary = primary(candidate).ok_or_else(|| DomainError::uncaught("StopIteration", ""))?;
        if !inherited.contains(primary.package_id()) {
            continue;
        }
        for setting in campaign_setting_entries(candidate.package_lock()) {
            if py_eq(&ruleset_id, &text(primary.package_id()))
                && py_eq(&setting_id, &text(setting.package_id()))
                && priority(setting.version()) <= priority(&bundled_setting_version)
                && !py_eq(&text(setting.sha256()), &bundled_setting_sha)
            {
                continue;
            }
            let ruleset_settings = snapshots.entry(primary.package_id().to_owned()).or_default();
            if let Some(previous) = ruleset_settings.get(setting.package_id()) {
                let previous_version = py_str(previous.get("version").unwrap_or(&Json::Null));
                if priority(setting.version()) <= priority(&previous_version) {
                    continue;
                }
            }
            ruleset_settings.insert(
                setting.package_id().to_owned(),
                serde_json::json!({
                    "id": setting.package_id(), "version": setting.version(), "content_sha256": setting.sha256(),
                    "candidate_id": candidate.candidate_id(),
                    "ruleset": {"id": primary.package_id(), "version": primary.version(), "content_sha256": primary.sha256()},
                }),
            );
            let identity =
                (setting.package_id().to_owned(), setting.version().to_owned(), setting.sha256().to_owned());
            if !known_settings.contains(&identity) {
                items.push(setting_item(&identity, label(&identity.0, &identity.1, &identity.2)?));
                known_settings.insert(identity);
            }
        }
    }
    for (ruleset_id, settings) in &snapshots {
        let index = *ruleset_indexes
            .get(ruleset_id)
            .ok_or_else(|| DomainError::uncaught("KeyError", format!("'{ruleset_id}'")))?;
        let choice = &mut items[index];
        let mut combined: Vec<Json> = settings.values().cloned().collect();
        for binding in listed(choice, "compatible_settings") {
            let id = py_str(field(&object(&binding)?, "id")?);
            if !settings.contains_key(&id) {
                combined.push(binding);
            }
        }
        choice.insert("compatible_settings".into(), Json::Array(combined));
        let mut aliases: Vec<Json> = listed(choice, "selection_aliases");
        for candidate in &candidates {
            let owns = candidate
                .package_lock()
                .entries()
                .iter()
                .any(|entry| entry.role() == PackageRole::Primary && entry.package_id() == ruleset_id);
            if owns {
                aliases.push(text(candidate.candidate_id()));
            }
        }
        // `list(dict.fromkeys(...))`.
        let mut unique: Vec<Json> = Vec::new();
        for alias in aliases {
            if !unique.iter().any(|seen| py_eq(seen, &alias)) {
                unique.push(alias);
            }
        }
        choice.insert("selection_aliases".into(), Json::Array(unique));
    }
    let valid: BTreeSet<String> =
        items.iter().map(|item| py_str(item.get("id").unwrap_or(&Json::Null))).collect();
    for ((_, artifact_id), item) in unavailable {
        if !valid.contains(&artifact_id) {
            items.push(item);
        }
    }
    Ok(items)
}

fn setting_item(identity: &(String, String, String), title: String) -> JsonMap {
    let mut item = JsonMap::new();
    item.insert("kind".into(), text("campaign_setting"));
    item.insert("id".into(), text(&identity.0));
    item.insert("version".into(), text(&identity.1));
    item.insert("content_sha256".into(), text(&identity.2));
    item.insert("title".into(), Json::String(title));
    item
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_priority_orders_semantic_versions() {
        let ordered = [
            "0.9",
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0",
            "1.0.1",
            "1.10.0",
            "10.0.0",
        ];
        for pair in ordered.windows(2) {
            assert!(priority(pair[0]) < priority(pair[1]), "{} < {}", pair[0], pair[1]);
        }
        assert!(priority("not a version") < priority("0.0.0"));
        assert_eq!(priority("1.0.0+build").0, 1);
        assert_eq!(priority("1..0").0, 0);
        assert_eq!(priority("1.2.3.4").0, 0);
        // Python's `\d` and `int()` read other scripts' decimal digits; the last field stays the
        // casefolded text itself.
        let arabic = priority("\u{0661}.\u{0662}");
        assert_eq!((arabic.0, arabic.1, arabic.2), (1, Number::parse("1"), Number::parse("2")));
        assert!(priority("\u{FF11}0.0") > priority("9.0"));
    }
}
