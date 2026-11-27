//! The GM's campaign knowledge reads, from `application/authority.py`:
//! `AuthorityOwner.search_campaign_knowledge` and `read_campaign_reference`, with
//! `_markdown_sections`, `_select_markdown_section`, `_markdown_section_references`,
//! `_structured_campaign_plan_outline` and `_read_structured_campaign_plan`.
//!
//! Search discovers bounded exact references (Campaign State facts and entities, Character
//! Files, the accepted Plan's sections and items, and the bound Setting and Ruleset packages'
//! text files) and ranks them with `knowledge_search`; a read returns one exact reference within
//! its byte bound. Both are authority reads: nothing is written.
//!
//! Each takes its arguments as the JSON values its caller received, so the owner's own type
//! checks refuse with Python's codes whatever the caller already checked. A path or source that
//! is not text where Python calls a string method fails as Python's `AttributeError` does.

use super::actions::ActionError;
use crate::knowledge_search::{bounded_search_page, knowledge_terms, rank_references, KnowledgeReference};
use crate::reference_text::{
    matching_text_excerpts, text_excerpt, TextExcerpt, REFERENCE_CONTENT_BYTE_LIMIT,
    REFERENCE_REPLY_BYTE_LIMIT,
};
use std::collections::BTreeMap;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pystr;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{validate_authority_identity, PackageLockEntry};
use sylvia_domain::package_archive::{PACKAGE_MEMBER_BYTE_LIMIT, PACKAGE_MEMBER_COUNT_LIMIT};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::{py_str, py_type_name};
use sylvia_domain::DomainError;
use sylvia_store::work::CampaignWork;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

/// What this module's reads answer: Python's uncaught classes stay [`ActionError::Domain`].
type Answer<T> = Result<T, ActionError>;

/// `_CAMPAIGN_KNOWLEDGE_SOURCES`, sorted.
const SOURCES: [&str; 5] = ["campaign_plan", "campaign_setting", "campaign_state", "participant", "ruleset"];

/// `{item.rstrip("s") for item in _PRIVATE_PREPARATION_HEADINGS}`.
const PRIVATE_HEADING_KEYS: [&str; 3] = ["encounter", "hook", "secret"];

/// `_STRUCTURED_OUTLINE_INLINE_BYTES`.
const OUTLINE_INLINE_BYTES: [usize; 2] = [4096, 256];

/// `search_campaign_knowledge(campaign_id, query=..., sources=..., limit=..., offset=...)`'s
/// arguments, as given (Python's defaults are `sources=None`, `limit=5`, `offset=0`).
#[derive(Debug, Clone, PartialEq)]
pub struct KnowledgeSearch {
    pub query: Json,
    pub sources: Option<Json>,
    pub limit: Json,
    pub offset: Json,
}

fn rejected(message: &str) -> ActionError {
    DomainError::command_rejected(message).into()
}

fn rejected_with(message: &str, code: &str) -> ActionError {
    DomainError::command_rejected_with(message, code, Vec::new()).into()
}

fn uncaught(class: &'static str, message: impl Into<String>) -> ActionError {
    DomainError::uncaught(class, message.into()).into()
}

/// `value.method(...)` on a value that may not be text: Python's `AttributeError` for another
/// type.
fn text<'a>(value: &'a Json, method: &str) -> Answer<&'a str> {
    value.as_str().ok_or_else(|| {
        uncaught("AttributeError", format!("'{}' object has no attribute '{method}'", py_type_name(value)))
    })
}

/// An `int` that is not a `bool`, within `i64`.
fn py_int(value: &Json) -> Option<i64> {
    match value {
        Json::Number(number) => number.as_i64(),
        _ => None,
    }
}

/// Python's truth value of a JSON value.
fn truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(flag) => *flag,
        Json::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Json::String(text) => !text.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(map) => !map.is_empty(),
    }
}

fn canonical_text(value: &Json) -> String {
    String::from_utf8(canonical_json_bytes(value)).unwrap_or_default()
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// `str.title()`: each cased character upper after an uncased one, lower after a cased one.
fn title(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut previous_cased = false;
    for character in value.chars() {
        let cased = character.is_uppercase() || character.is_lowercase();
        if cased && previous_cased {
            out.extend(character.to_lowercase());
        } else if cased {
            out.extend(character.to_uppercase());
        } else {
            out.push(character);
        }
        previous_cased = cased;
    }
    out
}

/// `text.rstrip("s")`.
fn rstrip_s(value: &str) -> &str {
    value.trim_end_matches('s')
}

/// Whether a heading names private preparation (`heading.casefold().rstrip("s")` in the set).
fn private_heading(heading: &str) -> bool {
    PRIVATE_HEADING_KEYS.contains(&rstrip_s(&pystr::casefold(heading)))
}

/// `re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")`.
fn slug(value: &str) -> String {
    let mut out = String::new();
    let mut run = false;
    for character in pystr::casefold(value).chars() {
        if character.is_ascii_lowercase() || character.is_ascii_digit() {
            out.push(character);
            run = false;
        } else if !run {
            out.push('-');
            run = true;
        }
    }
    out.trim_matches('-').to_owned()
}

/// `Path(path).name`, `.suffix` and `.stem` for a portable path.
fn name_parts(path: &str) -> (&str, &str) {
    let name = path.rsplit('/').next().unwrap_or(path);
    match name.rfind('.') {
        Some(index) if index > 0 && index < name.len() - 1 => (&name[..index], &name[index..]),
        _ => (name, ""),
    }
}

/// The package label of a path: `Path(path).stem.replace("-", " ").title()`.
fn stem_label(path: &str) -> String {
    title(&name_parts(path).0.replace('-', " "))
}

/// `_markdown_sections(value)`: each `#`..`######` heading's text, slug and section (its line
/// to the next heading at its level or above), stripped.
pub(crate) fn markdown_sections(value: &str) -> Vec<(String, String, String)> {
    let lines = pystr::splitlines(value);
    let mut headings: Vec<(usize, usize, String)> = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        // `re.match(r"^(#{1,6})\s+(.+?)\s*$", line)`.
        let level = line.chars().take_while(|character| *character == '#').count();
        if level == 0 || level > 6 {
            continue;
        }
        let rest = &line[level..];
        let Some(first) = rest.chars().next() else { continue };
        if !pystr::is_space(first) {
            continue;
        }
        let stripped = pystr::strip(rest);
        if stripped.is_empty() && rest.chars().count() < 2 {
            continue;
        }
        headings.push((index, level, stripped.to_owned()));
    }
    let mut ends = vec![lines.len(); headings.len()];
    let mut parents: Vec<usize> = Vec::new();
    for (index, (start, level, _)) in headings.iter().enumerate() {
        while let Some(parent) = parents.last() {
            if headings[*parent].1 < *level {
                break;
            }
            ends[*parent] = *start;
            parents.pop();
        }
        parents.push(index);
    }
    headings
        .iter()
        .zip(ends)
        .map(|((start, _, heading), end)| {
            let content = lines[*start..end].join("\n");
            (heading.clone(), slug(heading), pystr::strip(&content).to_owned())
        })
        .collect()
}

/// How many sections share each slug (`Counter(slug for ...)`).
fn slug_counts(sections: &[(String, String, String)]) -> BTreeMap<&str, usize> {
    let mut counts = BTreeMap::new();
    for (_, slug, _) in sections {
        *counts.entry(slug.as_str()).or_insert(0) += 1;
    }
    counts
}

/// `_select_markdown_section(value, fragment)`: the one section whose slug the fragment names.
fn select_markdown_section(value: &str, fragment: &str) -> Answer<(String, String)> {
    let normalized = slug(fragment);
    let mut matches: Vec<(String, String)> = markdown_sections(value)
        .into_iter()
        .filter(|(_, slug, _)| *slug == normalized)
        .map(|(heading, _, content)| (heading, content))
        .collect();
    if matches.len() != 1 {
        return Err(rejected("Campaign reference section is unavailable"));
    }
    Ok(matches.remove(0))
}

/// `_markdown_section_references(value, path=..., document_private=...)`: at most 100 unique
/// section references within 8 KiB, and how many sections there are.
fn markdown_section_references(value: &str, path: &str, document_private: bool) -> (Vec<Json>, i64) {
    let sections = markdown_sections(value);
    let counts = slug_counts(&sections);
    let whole = pystr::strip(value);
    let mut references: Vec<Json> = Vec::new();
    let mut total = 0;
    for (heading, slug, content) in &sections {
        if sections.len() > 1 && content == whole {
            continue;
        }
        total += 1;
        if references.len() >= 100 || pystr::len(slug) > 200 || counts[slug.as_str()] != 1 {
            continue;
        }
        let private = document_private || private_heading(heading);
        let reference = Json::Object(object(vec![
            ("heading", heading.chars().take(200).collect::<String>().into()),
            ("path", format!("{path}#{slug}").into()),
            ("visibility", if private { "gm_private_preparation" } else { "campaign_reference" }.into()),
        ]));
        let mut candidate = references.clone();
        candidate.push(reference);
        if canonical_json_bytes(&Json::Array(candidate.clone())).len() <= 8 * 1024 {
            references = candidate;
        }
    }
    (references, total)
}

/// `_structured_campaign_plan_outline(root, inline_bytes=...)`: short fields kept, long ones
/// replaced by their exact section read paths.
fn structured_outline(root: &JsonMap, inline_bytes: usize) -> JsonMap {
    let mut outline = JsonMap::new();
    for (key, value) in root {
        if canonical_json_bytes(value).len() <= inline_bytes {
            outline.insert(key.clone(), value.clone());
            continue;
        }
        let identities: Vec<Option<&Json>> = match value {
            Json::Array(items) => {
                items.iter().filter_map(|item| item.as_object()).map(|item| item.get("id")).collect()
            }
            _ => Vec::new(),
        };
        let all_ids = !identities.is_empty()
            && identities.iter().all(|item| item.and_then(Json::as_str).is_some_and(|id| !id.is_empty()))
            && value.as_array().is_some_and(|items| items.len() == identities.len());
        let paths: Vec<Json> = if all_ids {
            identities
                .iter()
                .map(|id| format!("structured/{key}/{}", id.unwrap().as_str().unwrap()).into())
                .collect()
        } else {
            vec![format!("structured/{key}").into()]
        };
        outline.insert(key.clone(), Json::Object(object(vec![("read_via", Json::Array(paths))])));
    }
    outline
}

/// `_read_structured_campaign_plan(root, path)`: one exact section; an act read includes its
/// linked completion requirements.
fn read_structured(root: &JsonMap, path: &str) -> Answer<Json> {
    let unavailable = || rejected("Campaign Plan reference path is unavailable");
    let segments: Vec<&str> = path.split('/').filter(|item| !item.is_empty()).collect();
    let mut current = Json::Object(root.clone());
    for segment in &segments {
        current = match &current {
            Json::Object(map) => map.get(*segment).cloned().ok_or_else(unavailable)?,
            Json::Array(items) => items
                .iter()
                .find(|item| {
                    item.as_object().is_some_and(|item| item.get("id") == Some(&Json::from(*segment)))
                })
                .cloned()
                .ok_or_else(unavailable)?,
            _ => return Err(unavailable()),
        };
    }
    let Json::Object(act) = &current else { return Ok(current) };
    if !(segments.len() == 2 && segments[0] == "structure") {
        return Ok(current);
    }
    let unavailable = || rejected("Campaign act completion references are unavailable");
    let (Some(Json::Array(declared)), Some(Json::Array(required))) =
        (root.get("objectives"), act.get("objectives"))
    else {
        return Err(unavailable());
    };
    let mut objectives: Vec<(String, Json)> = Vec::new();
    for item in declared {
        if let Some(Json::String(id)) = item.as_object().and_then(|item| item.get("id")) {
            match objectives.iter_mut().find(|(key, _)| key == id) {
                Some(slot) => slot.1 = item.clone(),
                None => objectives.push((id.clone(), item.clone())),
            }
        }
    }
    let ending = root.get("ending");
    let final_act =
        matches!(root.get("structure"), Some(Json::Array(structure)) if structure.last() == Some(&current));
    let ongoing_final = final_act
        && ending
            .and_then(Json::as_object)
            .is_some_and(|ending| ending.get("mode") == Some(&Json::from("ongoing")));
    let objective = |id: &Json| {
        id.as_str().and_then(|id| objectives.iter().find(|(key, _)| key == id).map(|(_, item)| item))
    };
    if (required.is_empty() && !ongoing_final) || required.iter().any(|id| objective(id).is_none()) {
        return Err(unavailable());
    }
    let mut out = object(vec![
        ("act", current.clone()),
        ("required_objectives", Json::Array(required.iter().map(|id| objective(id).unwrap().clone()).collect())),
        (
            "completion_rule",
            if ongoing_final {
                "This final act is ongoing. Do not resolve it or invent a campaign ending."
            } else {
                "All of this act's linked objectives are required. All completion conditions of each objective \
                 must be met by played facts, including its closing event; choose one alternative only where a \
                 condition explicitly permits it. Side work neither replaces a required objective nor adds another \
                 prerequisite. An authored failure ending may resolve the act; deferring or retiring work alone \
                 does not mean that the act was completed."
            }
            .into(),
        ),
    ]);
    if final_act {
        if let Some(Json::Object(ending)) = ending {
            out.insert("campaign_ending".into(), Json::Object(ending.clone()));
        }
    }
    Ok(Json::Object(out))
}

/// `entry.dependencies` that are rules references.
fn reference_dependency_identities(entry: &PackageLockEntry) -> Vec<(String, String, String)> {
    entry
        .dependencies()
        .iter()
        .filter(|dependency| {
            dependency.kind() == "rules_reference" || dependency.namespace() == "rules_reference"
        })
        .map(|dependency| {
            (
                dependency.package_id().to_owned(),
                dependency.version().to_owned(),
                dependency.sha256().to_owned(),
            )
        })
        .collect()
}

fn identity_of(entry: &PackageLockEntry) -> (String, String, String) {
    (entry.package_id().to_owned(), entry.version().to_owned(), entry.sha256().to_owned())
}

/// A package read's `CommandRejected`, which search counts as an unavailable reference.
fn command_rejected_code(error: &StoreError) -> Option<String> {
    match error {
        StoreError::Coded(coded) if coded.error_type.unwrap_or("CommandRejected") == "CommandRejected" => {
            Some(coded.code.clone())
        }
        _ => None,
    }
}

/// `tuple(sources or sorted(_CAMPAIGN_KNOWLEDGE_SOURCES))`, checked: `None` for invalid sources.
fn requested_sources(value: Option<&Json>) -> Option<Vec<String>> {
    let items: Vec<String> = match value {
        None => return Some(SOURCES.iter().map(|item| (*item).to_owned()).collect()),
        Some(value) if !truthy(value) => {
            return Some(SOURCES.iter().map(|item| (*item).to_owned()).collect())
        }
        // A list of anything but text is never a valid source set.
        Some(Json::Array(items)) => {
            items.iter().map(|item| item.as_str().map(str::to_owned)).collect::<Option<_>>()?
        }
        // `tuple("...")` is its characters; `tuple({...})` its keys.
        Some(Json::String(text)) => text.chars().map(String::from).collect(),
        Some(Json::Object(map)) => map.keys().cloned().collect(),
        Some(_) => return None,
    };
    let mut seen = items.clone();
    seen.sort();
    seen.dedup();
    if items.is_empty()
        || seen.len() != items.len()
        || !items.iter().all(|item| SOURCES.contains(&item.as_str()))
    {
        return None;
    }
    Some(items)
}

/// `AuthorityOwner.search_campaign_knowledge(campaign_id, query=..., sources=..., limit=...,
/// offset=...)`: one page of ranked exact references, never a second source of truth.
pub async fn search_campaign_knowledge(
    store: &StoreHandle,
    campaign_id: &str,
    request: &KnowledgeSearch,
) -> Result<JsonMap, ActionError> {
    let limit = match py_int(&request.limit) {
        Some(limit) if (1..=8).contains(&limit) => limit,
        _ => {
            return Err(rejected_with(
                "Campaign knowledge parameter 'limit' must be an integer from 1 to 8",
                "campaign_knowledge_limit_invalid",
            ))
        }
    };
    let invalid =
        || rejected_with("Campaign knowledge search is invalid", "campaign_knowledge_search_invalid");
    let query = request.query.as_str().ok_or_else(invalid)?.to_owned();
    validate_authority_identity(&query, "Campaign knowledge query", false).map_err(|_| invalid())?;
    if pystr::len(&query) > 500 {
        return Err(invalid());
    }
    let sources = requested_sources(request.sources.as_ref()).ok_or_else(invalid)?;
    // `bounded_search_page` refuses an offset that is not an `int` the same way as one outside
    // the results.
    let offset = py_int(&request.offset).unwrap_or(-1);
    let campaign_id = campaign_id.to_owned();
    store.call(move |store| Ok(search_on(store, &campaign_id, &query, &sources, limit, offset))).await?
}

/// The candidates search ranks, with its unavailable references.
#[derive(Default)]
struct Found {
    candidates: Vec<KnowledgeReference>,
    unavailable: Vec<Json>,
    unavailable_count: i64,
}

impl Found {
    /// `add_match(...)`.
    #[allow(clippy::too_many_arguments)]
    fn add(
        &mut self,
        source: &str,
        path: &str,
        kind: &str,
        label: &str,
        visibility: &str,
        body: String,
        mut provenance: JsonMap,
        identity: Option<&str>,
    ) {
        if let Some(Json::String(heading)) = provenance.get("heading") {
            if pystr::len(heading) > 200 {
                let short: String = heading.chars().take(200).collect();
                provenance.insert("heading".into(), short.into());
                provenance.insert("heading_truncated".into(), true.into());
            }
        }
        let long = pystr::len(label) > 200;
        let mut result = object(vec![
            ("source", source.into()),
            ("path", path.into()),
            ("kind", kind.into()),
            ("label", label.chars().take(200).collect::<String>().into()),
        ]);
        if long {
            result.insert("label_truncated".into(), true.into());
        }
        result.insert("visibility".into(), visibility.into());
        result.insert("provenance".into(), Json::Object(provenance));
        let base = identity.filter(|identity| !identity.is_empty()).unwrap_or(path);
        let identity = if long { format!("{base} {label}") } else { base.to_owned() };
        self.candidates.push(KnowledgeReference { result, identity, body });
    }

    fn unavailable(&mut self, source: &str, path: &str, code: &str) {
        self.unavailable_count += 1;
        if self.unavailable.len() < 8 {
            self.unavailable.push(Json::Object(object(vec![
                ("source", source.into()),
                ("path", path.into()),
                ("code", code.into()),
            ])));
        }
    }
}

fn search_on(
    store: &AuthorityStore,
    campaign_id: &str,
    query: &str,
    sources: &[String],
    limit: i64,
    offset: i64,
) -> Answer<JsonMap> {
    let requested = |source: &str| sources.iter().any(|item| item == source);
    let terms = knowledge_terms(query);
    let snapshot = store.snapshot(campaign_id)?;
    let revision = snapshot.state.revision;
    let mut found = Found::default();
    if requested("campaign_state") {
        for item in &snapshot.continuity.facts {
            let Some(Json::String(record_id)) = item.get("record_id") else { continue };
            let is_entity = matches!(item.get("entity_kind"), Some(Json::String(_)));
            let label = match if is_entity { item.get("name") } else { None } {
                Some(Json::String(name)) => name.clone(),
                _ => record_id.clone(),
            };
            let visibility = match item.get("visibility") {
                Some(Json::String(visibility)) => visibility.clone(),
                _ => "gm_private".to_owned(),
            };
            found.add(
                "campaign_state",
                record_id,
                if is_entity { "entity" } else { "fact" },
                &label,
                &visibility,
                canonical_text(&Json::Object(item.clone())),
                object(vec![
                    ("campaign_id", campaign_id.into()),
                    ("campaign_revision", revision.into()),
                    ("record_id", record_id.as_str().into()),
                ]),
                None,
            );
        }
    }
    if requested("participant") {
        for participant in &snapshot.state.participants {
            let label = match participant.data().get("name") {
                Some(Json::String(name)) => name.clone(),
                _ => participant.participant_id().to_owned(),
            };
            found.add(
                "participant",
                participant.participant_id(),
                "character_file",
                &label,
                "gm_private_character_file",
                canonical_text(&Json::Object(participant.data().clone())),
                object(vec![
                    ("campaign_id", campaign_id.into()),
                    ("participant_id", participant.participant_id().into()),
                    ("package_id", participant.package_id().into()),
                    ("package_version", participant.package_version().into()),
                    ("package_sha256", participant.package_sha256().map_or(Json::Null, Json::from)),
                ]),
                None,
            );
        }
    }
    let accepted = store.accepted_campaign_plan(campaign_id)?;
    let plan: JsonMap = match accepted.as_ref().and_then(|plan| plan.content().get("campaign_plan")) {
        Some(Json::Object(plan)) => plan.clone(),
        _ => JsonMap::new(),
    };
    if let (Some(accepted), true) = (&accepted, !plan.is_empty() && requested("campaign_plan")) {
        let (plan_id, content_sha256) =
            (accepted.fields().plan_id.as_str(), accepted.fields().content_sha256.as_str());
        let plan_provenance = |extra: Vec<(&str, Json)>| {
            let mut provenance = object(vec![
                ("campaign_id", campaign_id.into()),
                ("plan_id", plan_id.into()),
                ("content_sha256", content_sha256.into()),
            ]);
            provenance.extend(extra.into_iter().map(|(key, value)| (key.to_owned(), value)));
            provenance
        };
        for (key, value) in &plan {
            let path = format!("structured/{key}");
            found.add(
                "campaign_plan",
                &path,
                "plan_section",
                &title(&key.replace('_', " ")),
                "gm_private_preparation",
                canonical_text(value),
                plan_provenance(vec![("section", key.as_str().into())]),
                None,
            );
            let Json::Array(items) = value else { continue };
            for item in items {
                let Some(item) = item.as_object() else { continue };
                let Some(Json::String(item_id)) = item.get("id") else { continue };
                // `item.get("title", item.get("name", item_id))`: a present key wins whatever it holds.
                let raw = item.get("title").or_else(|| item.get("name"));
                let label = match raw {
                    Some(Json::String(label)) => label.clone(),
                    _ => item_id.clone(),
                };
                found.add(
                    "campaign_plan",
                    &format!("{path}/{item_id}"),
                    "plan_item",
                    &label,
                    "gm_private_preparation",
                    canonical_text(&Json::Object(item.clone())),
                    plan_provenance(vec![
                        ("section", key.as_str().into()),
                        ("item_id", item_id.as_str().into()),
                    ]),
                    None,
                );
            }
        }
        if let Some(Json::String(readable)) = accepted.content().get("campaign_plan_markdown") {
            let sections = markdown_sections(readable);
            let counts = slug_counts(&sections);
            let whole = pystr::strip(readable);
            for (heading, slug, content) in &sections {
                if counts[slug.as_str()] != 1 || (sections.len() > 1 && content == whole) {
                    continue;
                }
                found.add(
                    "campaign_plan",
                    &format!("readable#{slug}"),
                    "plan_section",
                    heading,
                    "gm_private_preparation",
                    content.clone(),
                    plan_provenance(vec![("heading", heading.as_str().into())]),
                    None,
                );
            }
        }
    }
    let bindings = if plan.is_empty() { None } else { plan.get("bindings") };
    let mut private_references: Vec<String> = Vec::new();
    if !plan.is_empty() && requested("campaign_setting") {
        private_references = super::content_reads::campaign_setting_knowledge(store, campaign_id, &plan)?.1;
    }
    let lock = &snapshot.state.package_lock;
    for source in ["campaign_setting", "ruleset"] {
        let Some(Json::Object(bindings)) = bindings.filter(|_| requested(source)) else { continue };
        let Some(Json::Object(binding)) = bindings.get(source) else { continue };
        let (Some(Json::String(id)), Some(Json::String(version)), Some(Json::String(sha256))) =
            (binding.get("id"), binding.get("version"), binding.get("content_sha256"))
        else {
            continue;
        };
        let mut entries: Vec<PackageLockEntry> = lock
            .entries()
            .iter()
            .filter(|entry| entry.identity() == (id.as_str(), version.as_str(), sha256.as_str()))
            .cloned()
            .collect();
        if entries.is_empty() {
            if let Some(compatible) = store.compatible_package_binding(campaign_id, (id, version, sha256))? {
                if lock.entries().contains(&compatible) {
                    entries.push(compatible);
                }
            }
        }
        if source == "ruleset" && !entries.is_empty() {
            let dependencies = reference_dependency_identities(&entries[0]);
            let extra: Vec<PackageLockEntry> = lock
                .entries()
                .iter()
                .filter(|entry| dependencies.contains(&identity_of(entry)))
                .cloned()
                .collect();
            entries.extend(extra);
        }
        for entry in &entries {
            let paths = store.package_file_paths(
                entry.package_id(),
                entry.version(),
                entry.sha256(),
                PACKAGE_MEMBER_COUNT_LIMIT as i64,
            )?;
            for path in &paths {
                let suffix = pystr::casefold(name_parts(path).1);
                if ![".json", ".md", ".txt", ".yaml", ".yml"].contains(&suffix.as_str()) {
                    continue;
                }
                let value = match store.package_file_bytes(
                    entry.package_id(),
                    entry.version(),
                    entry.sha256(),
                    path,
                    PACKAGE_MEMBER_BYTE_LIMIT as i64,
                ) {
                    Ok(value) => value,
                    Err(error) => match command_rejected_code(&error) {
                        Some(code) => {
                            let code = if code.is_empty() {
                                "campaign_reference_unavailable".to_owned()
                            } else {
                                code
                            };
                            found.unavailable(source, path, &code);
                            continue;
                        }
                        None => return Err(error.into()),
                    },
                };
                let Some(value) = value else { continue };
                let Ok(text) = std::str::from_utf8(&value) else {
                    found.unavailable(source, path, "campaign_reference_not_utf8");
                    continue;
                };
                let package_provenance = |extra: Vec<(&str, Json)>| {
                    let mut provenance = object(vec![
                        ("campaign_id", campaign_id.into()),
                        ("package_id", entry.package_id().into()),
                        ("package_version", entry.version().into()),
                        ("package_sha256", entry.sha256().into()),
                        ("source_path", path.as_str().into()),
                    ]);
                    provenance.extend(extra.into_iter().map(|(key, value)| (key.to_owned(), value)));
                    provenance
                };
                let small = value.len() as i64 <= REFERENCE_CONTENT_BYTE_LIMIT;
                let sections =
                    if small && path.ends_with(".md") { markdown_sections(text) } else { Vec::new() };
                let long_heading = sections
                    .iter()
                    .any(|(heading, slug, _)| pystr::len(heading) > 200 || pystr::len(slug) > 200);
                if !small || long_heading {
                    let visibility = if source == "campaign_setting" {
                        "gm_private_preparation"
                    } else {
                        "campaign_reference"
                    };
                    for (text_offset, excerpt) in matching_text_excerpts(text, query, limit) {
                        found.add(
                            source,
                            &format!("{path}#text/{text_offset}"),
                            "package_excerpt",
                            &stem_label(path),
                            visibility,
                            excerpt,
                            package_provenance(vec![("text_offset", text_offset.into())]),
                            Some(path),
                        );
                    }
                    continue;
                }
                let document_private = private_references.contains(path);
                if sections.is_empty() {
                    found.add(
                        source,
                        path,
                        "package_reference",
                        &stem_label(path),
                        if document_private { "gm_private_preparation" } else { "campaign_reference" },
                        text.to_owned(),
                        package_provenance(Vec::new()),
                        None,
                    );
                    continue;
                }
                let path_terms = knowledge_terms(&name_parts(path).0.replace('-', " "));
                let counts = slug_counts(&sections);
                let whole = pystr::strip(text);
                for (heading, slug, content) in &sections {
                    if counts[slug.as_str()] != 1 || (sections.len() > 1 && content == whole) {
                        continue;
                    }
                    let private = document_private || private_heading(heading);
                    if private && terms.iter().all(|term| path_terms.contains(term)) {
                        continue;
                    }
                    found.add(
                        source,
                        &format!("{path}#{slug}"),
                        "authored_section",
                        heading,
                        if private { "gm_private_preparation" } else { "campaign_reference" },
                        content.clone(),
                        package_provenance(vec![("heading", heading.as_str().into())]),
                        Some(&format!("{path} {heading}")),
                    );
                }
            }
        }
    }
    let matches = rank_references(query, &found.candidates)?;
    let metadata = object(vec![
        ("query", query.into()),
        ("sources", Json::Array(sources.iter().map(|item| Json::from(item.as_str())).collect())),
        ("campaign_revision", revision.into()),
        ("unavailable_count", found.unavailable_count.into()),
        ("unavailable_references", Json::Array(found.unavailable)),
        (
            "authority",
            "Discovery returns references only. Read the selected exact reference; Campaign State remains \
             current truth."
                .into(),
        ),
    ]);
    Ok(bounded_search_page(&metadata, &matches, offset, limit, REFERENCE_REPLY_BYTE_LIMIT)?)
}

/// `AuthorityOwner.read_campaign_reference(campaign_id, source=..., path=...,
/// maximum_bytes=...)`: one bounded reference from the campaign's exact admitted authority.
pub async fn read_campaign_reference(
    store: &StoreHandle,
    campaign_id: &str,
    source: &Json,
    path: &Json,
    maximum_bytes: Option<&Json>,
) -> Result<JsonMap, ActionError> {
    let maximum = match maximum_bytes {
        None => REFERENCE_REPLY_BYTE_LIMIT,
        Some(value) => match py_int(value) {
            Some(maximum) if (1..=REFERENCE_REPLY_BYTE_LIMIT).contains(&maximum) => maximum,
            _ => {
                return Err(rejected_with(
                    "Reference reply limit must be from 1 to 65536 bytes",
                    "campaign_reference_limit_invalid",
                ))
            }
        },
    };
    let (campaign_id, source, path, work) =
        (campaign_id.to_owned(), source.clone(), path.clone(), store.lanes().work.clone());
    store.call(move |store| Ok(read_on(store, &work, &campaign_id, &source, &path, maximum))).await?
}

/// A reply whose content must fit `maximum` bytes.
fn too_large(content: &str, maximum: i64) -> bool {
    content.len() as i64 > maximum
}

fn read_on(
    store: &AuthorityStore,
    work: &CampaignWork,
    campaign_id: &str,
    source: &Json,
    path: &Json,
    maximum: i64,
) -> Answer<JsonMap> {
    let snapshot = store.snapshot(campaign_id)?;
    let source_text = source.as_str();
    if source_text == Some("engine_format") {
        if path.as_str() != Some("spatial_scene") {
            return Err(rejected("Engine format reference is unavailable"));
        }
        let definition = sylvia_domain::content::catalog::get_content_definition("spatial_scene")?;
        let content = canonical_json_bytes(&Json::Object(definition));
        if content.len() as i64 > maximum {
            return Err(rejected("Engine format reference exceeds the read bound"));
        }
        return Ok(object(vec![
            ("source", source.clone()),
            ("path", path.clone()),
            ("campaign_id", campaign_id.into()),
            ("content", String::from_utf8(content.clone()).unwrap_or_default().into()),
            ("content_sha256", sha256_hex(&content).into()),
            (
                "provenance",
                Json::Object(object(vec![
                    ("owner", "sylvia.application".into()),
                    ("definition", path.clone()),
                ])),
            ),
            (
                "precedence",
                "Installed engine input format only; this is not campaign state or authority to invent actors, \
                 positions, or outcomes."
                    .into(),
            ),
        ]));
    }
    if source_text == Some("campaign_state") {
        let projected = match path.as_str() {
            Some("current") => {
                // `desktop_snapshot(campaign_id, display_only=True).state`: engine-private keys left out.
                let display =
                    store.display_snapshot(campaign_id, &sylvia_store::snapshots::DisplayQuery::default())?;
                let private = super::snapshots::engine_private_keys(store, &display.state.package_lock)?;
                let state: JsonMap =
                    display.state.state.into_iter().filter(|(key, _)| !private.contains(key)).collect();
                let continuity = &snapshot.continuity;
                Json::Object(object(vec![
                    ("current_scene", continuity.current_scene.clone().map_or(Json::Null, Json::Object)),
                    (
                        "facts_and_entities",
                        Json::Array(continuity.facts.iter().cloned().map(Json::Object).collect()),
                    ),
                    ("reveals", Json::Object(continuity.reveals.clone())),
                    (
                        "plan_progress",
                        Json::Array(continuity.plan_progress.iter().cloned().map(Json::Object).collect()),
                    ),
                    ("ruleset_owned_values", Json::Object(state)),
                ]))
            }
            Some("inventory") => {
                work.ensure_not_transitioning(campaign_id)?;
                let tool = super::turn_reads::inventory_tool(store, campaign_id)?;
                let binding =
                    tool.as_ref().map(|tool| tool.input_schema.get("x-sylvia-gm-inventory").cloned());
                let binding = match binding {
                    Some(Some(binding)) => Some(binding),
                    Some(None) => return Err(uncaught("KeyError", "'x-sylvia-gm-inventory'")),
                    None => None,
                };
                let mechanical = match &binding {
                    Some(Json::Object(binding)) => binding.get("format") == Some(&Json::from("mechanics")),
                    Some(other) => {
                        return Err(uncaught(
                            "AttributeError",
                            format!("'{}' object has no attribute 'get'", py_type_name(other)),
                        ))
                    }
                    None => false,
                };
                let holdings = if tool.is_none() {
                    Json::Array(
                        crate::campaign_inventory::inventory_holdings(&snapshot)?
                            .into_iter()
                            .map(Json::Object)
                            .collect(),
                    )
                } else {
                    Json::Null
                };
                let mut projected = object(vec![
                    ("mode", if tool.is_some() { "ruleset" } else { "fallback" }.into()),
                    ("campaign_revision", snapshot.state.revision.into()),
                    ("holdings", holdings),
                    ("ruleset_inventory", binding.clone().unwrap_or(Json::Null)),
                ]);
                if mechanical {
                    let tools = binding
                        .as_ref()
                        .and_then(Json::as_object)
                        .and_then(|binding| binding.get("tools"))
                        .cloned()
                        .ok_or_else(|| uncaught("KeyError", "'tools'"))?;
                    projected.insert("update_tools".into(), tools);
                }
                projected.insert(
                    "meaning".into(),
                    if mechanical {
                        "Ruleset-owned inventory. Read the declared Campaign State or participant current_state and \
                         use its Ruleset tools to change it. Fallback inventory updates are unavailable."
                    } else {
                        "Committed inventory. Query participant current_state for Ruleset fields."
                    }
                    .into(),
                );
                Json::Object(projected)
            }
            _ => snapshot
                .continuity
                .facts
                .iter()
                .find(|fact| fact.get("record_id") == Some(path))
                .cloned()
                .map(Json::Object)
                .ok_or_else(|| rejected("Campaign State reference is unavailable"))?,
        };
        let content = canonical_text(&projected);
        if too_large(&content, maximum) {
            return Err(rejected_with(
                "Campaign State reference exceeds the read bound",
                "campaign_reference_too_large",
            ));
        }
        return Ok(object(vec![
            ("source", source.clone()),
            ("path", path.clone()),
            ("campaign_id", campaign_id.into()),
            ("campaign_revision", snapshot.state.revision.into()),
            ("content", content.into()),
            (
                "precedence",
                "Campaign State is current campaign truth and overrides Plan preparation and Setting defaults".into(),
            ),
        ]));
    }
    if source_text == Some("participant") {
        return read_participant(store, work, campaign_id, &snapshot, source, path, maximum);
    }
    let Some(mut accepted) = store.accepted_campaign_plan(campaign_id)? else {
        return Err(rejected("Campaign reference source requires an accepted plan"));
    };
    let mut path_value = path.clone();
    if source_text == Some("campaign_plan") && path.as_str() == Some("adventures") {
        let adventures = store.campaign_adventures(campaign_id)?;
        let mut items = Vec::new();
        for (index, item) in adventures.iter().enumerate() {
            let plan = match item.content().get("campaign_plan") {
                Some(Json::Object(plan)) => plan,
                Some(_) => return Err(uncaught("TypeError", "the campaign plan is not subscriptable")),
                None => return Err(uncaught("KeyError", "'campaign_plan'")),
            };
            let field =
                |key: &str| plan.get(key).cloned().ok_or_else(|| uncaught("KeyError", format!("'{key}'")));
            let source_id = match item.content().get("sequel") {
                None => Json::Null,
                Some(Json::Object(sequel)) => sequel.get("source_id").cloned().unwrap_or(Json::Null),
                Some(other) => {
                    return Err(uncaught(
                        "AttributeError",
                        format!("'{}' object has no attribute 'get'", py_type_name(other)),
                    ))
                }
            };
            items.push(Json::Object(object(vec![
                ("number", (index as i64 + 1).into()),
                ("plan_id", item.fields().plan_id.as_str().into()),
                ("content_sha256", item.fields().content_sha256.as_str().into()),
                ("title", field("title")?),
                ("source_id", source_id),
                ("structure", field("structure")?),
            ])));
        }
        let content = canonical_text(&Json::Array(items));
        if too_large(&content, maximum) {
            return Err(rejected("Adventure sequence exceeds the read bound"));
        }
        return Ok(object(vec![
            ("source", source.clone()),
            ("path", path.clone()),
            ("content", content.into()),
            ("campaign_id", campaign_id.into()),
            ("visibility", "gm_private_preparation".into()),
        ]));
    }
    if source_text == Some("campaign_plan") && text(path, "startswith")?.starts_with("adventures/") {
        let rest = &path.as_str().unwrap_or_default()["adventures/".len()..];
        let digits = rest.chars().take_while(char::is_ascii_digit).count();
        let (number, tail) = rest.split_at(digits);
        let valid_tail = tail.strip_prefix('/').filter(|tail| {
            *tail == "markdown"
                || *tail == "structured"
                || tail.strip_prefix("structured/").is_some_and(|rest| !rest.contains('\n'))
        });
        let adventures = store.campaign_adventures(campaign_id)?;
        let index = (digits > 0 && !number.starts_with('0'))
            .then(|| number.parse::<usize>().ok())
            .flatten()
            .filter(|number| *number <= adventures.len());
        let (Some(tail), Some(index)) = (valid_tail, index) else {
            return Err(rejected("Retained adventure reference is unavailable"));
        };
        accepted = adventures[index - 1].clone();
        path_value = Json::from(if tail == "markdown" { "readable" } else { tail });
    }
    let structured = accepted.content().get("campaign_plan").cloned();
    if source_text == Some("campaign_plan") {
        return read_plan(&accepted, structured.as_ref(), campaign_id, source, &path_value, maximum);
    }
    read_package(store, campaign_id, &snapshot, structured.as_ref(), source, &path_value, maximum)
}

#[allow(clippy::too_many_arguments)]
fn read_participant(
    store: &AuthorityStore,
    work: &CampaignWork,
    campaign_id: &str,
    snapshot: &sylvia_domain::authority::CampaignSnapshot,
    source: &Json,
    path: &Json,
    maximum: i64,
) -> Answer<JsonMap> {
    let Some(participant) =
        snapshot.state.participants.iter().find(|item| path.as_str() == Some(item.participant_id()))
    else {
        return Err(rejected("Participant reference is unavailable"));
    };
    let mut selector_summary: Option<String> = None;
    let mut authored_profile: Option<String> = None;
    let mut setting_data = JsonMap::new();
    let mut profile_status = "sparse";
    let mut source_character_file_id: Option<String> = None;
    let mut party_role = participant.data().get("party_role").cloned().unwrap_or(Json::Null);
    let construction = match store.campaign_construction_basis(campaign_id) {
        Ok(construction) => Some(construction),
        Err(error) if command_rejected_code(&error).is_some() => None,
        Err(error) => return Err(error.into()),
    };
    if let Some(Json::Array(selected)) =
        construction.as_ref().and_then(|basis| basis.canonical_input.get("selected_party"))
    {
        let record = selected
            .iter()
            .filter_map(Json::as_object)
            .find(|item| item.get("participant_id") == Some(&Json::from(participant.participant_id())));
        if let Some(record) = record {
            if let Some(Json::String(summary)) = record.get("summary") {
                selector_summary = Some(summary.clone());
            }
            if let Some(role) = record.get("party_role") {
                party_role = role.clone();
            }
            if let Some(Json::Object(character_file)) = record.get("character_file") {
                if let Some(Json::Object(data)) = character_file.get("setting_data") {
                    setting_data = data.clone();
                }
                if let Some(Json::String(id)) = character_file.get("source_id") {
                    if !id.is_empty() {
                        source_character_file_id = Some(id.clone());
                    }
                }
                if let (Some(Json::String(status)), Some(Json::String(profile))) =
                    (character_file.get("profile_status"), character_file.get("authored_profile"))
                {
                    if status == "authored" {
                        profile_status = "authored";
                        authored_profile = Some(profile.clone());
                    }
                }
            }
        }
    }
    let mut character_file = object(vec![
        ("participant_id", participant.participant_id().into()),
        ("party_role", party_role),
        (
            "profile",
            Json::Object(object(vec![
                ("status", profile_status.into()),
                ("authored_text", authored_profile.map_or(Json::Null, Json::from)),
            ])),
        ),
        ("setting_data", Json::Object(setting_data)),
        (
            "ruleset",
            Json::Object(object(vec![
                ("schema_kind", participant.kind().into()),
                ("starting_data", Json::Object(participant.data().clone())),
                (
                    "meaning",
                    "Ruleset-compatible starting data. Opaque identifiers establish only their exact stored values, \
                     not unwritten prose biography."
                        .into(),
                ),
            ])),
        ),
    ]);
    if let Some(summary) = selector_summary {
        character_file.insert(
            "selector".into(),
            Json::Object(object(vec![
                ("summary", summary.into()),
                ("meaning", "Short setup-catalog aid, not a substitute for authored biography.".into()),
            ])),
        );
    }
    if let Some(id) = source_character_file_id {
        character_file.insert("source_character_file_id".into(), id.into());
    }
    work.ensure_not_transitioning(campaign_id)?;
    if let Some(bookkeeping) = super::turn_reads::character_update_tool(store, campaign_id)? {
        let target =
            bookkeeping.input_schema.get("x-sylvia-gm-character-update").cloned().unwrap_or(Json::Null);
        let field =
            |key: &str| target.get(key).map(py_str).ok_or_else(|| uncaught("KeyError", format!("'{key}'")));
        let state_key = format!("{}.{}", field("namespace")?, field("key")?);
        let current = store.snapshot(campaign_id)?;
        let entity = match current.state.state.get(&state_key) {
            None => Json::Null,
            Some(Json::Object(holdings)) => {
                holdings.get(participant.participant_id()).cloned().unwrap_or(Json::Null)
            }
            Some(other) => {
                return Err(uncaught(
                    "AttributeError",
                    format!("'{}' object has no attribute 'get'", py_type_name(other)),
                ))
            }
        };
        work.ensure_not_transitioning(campaign_id)?;
        let options = super::turn_reads::character_update_options(store, campaign_id)?;
        let schema = options
            .get("character_update_schema")
            .cloned()
            .ok_or_else(|| uncaught("KeyError", "'character_update_schema'"))?;
        character_file.insert(
            "current_state".into(),
            Json::Object(object(vec![
                ("campaign_revision", current.state.revision.into()),
                ("fields", entity),
                ("update_schema", schema),
                ("meaning", "Current campaign holdings; starting_data is not live state.".into()),
            ])),
        );
    }
    let content = canonical_text(&Json::Object(character_file));
    if too_large(&content, maximum) {
        return Err(rejected("Participant reference exceeds the read bound"));
    }
    Ok(object(vec![
        ("source", source.clone()),
        ("path", path.clone()),
        ("campaign_id", campaign_id.into()),
        ("content", content.into()),
        (
            "precedence",
            "Campaign State and authoritative engine state override Character File values for this campaign"
                .into(),
        ),
    ]))
}

fn read_plan(
    accepted: &sylvia_domain::session::CampaignPlan,
    structured: Option<&Json>,
    campaign_id: &str,
    source: &Json,
    path_value: &Json,
    maximum: i64,
) -> Answer<JsonMap> {
    let path = text(path_value, "startswith")?;
    let structured_map = structured.and_then(Json::as_object);
    if path.starts_with("structured") && structured_map.is_none() {
        return Err(rejected("Campaign reference requires a structured plan"));
    }
    let readable = || match accepted.content().get("campaign_plan_markdown") {
        Some(Json::String(readable)) => Some(readable.as_str()),
        _ => None,
    };
    let (content, selection): (String, Option<JsonMap>) = if path == "structured" {
        let structured = structured_map.expect("checked above");
        let mut content = canonical_text(&Json::Object(structured.clone()));
        let mut selection = None;
        if too_large(&content, maximum) {
            // A whole Plan past the bound still answers the advertised read: its short fields
            // plus the exact section paths for the rest.
            for inline in OUTLINE_INLINE_BYTES {
                content = canonical_text(&Json::Object(structured_outline(structured, inline)));
                if !too_large(&content, maximum) {
                    break;
                }
            }
            selection = Some(object(vec![
                ("kind", "structured_outline".into()),
                (
                    "reason",
                    "The whole Plan exceeds the read bound. Long sections are replaced by read_via paths; read the \
                     one you need."
                        .into(),
                ),
            ]));
        }
        (content, selection)
    } else if let Some(section) = path.strip_prefix("structured/") {
        let selected = read_structured(structured_map.expect("checked above"), section)?;
        (
            canonical_text(&selected),
            Some(object(vec![("kind", "structured_section".into()), ("path", section.into())])),
        )
    } else if path == "readable" {
        (readable().ok_or_else(|| rejected("Readable Campaign Plan is unavailable"))?.to_owned(), None)
    } else if let Some(page) = path.strip_prefix("readable/page/") {
        let valid =
            (1..=10).contains(&page.len()) && page.chars().all(|character| character.is_ascii_digit());
        let (Some(readable), true) = (readable(), valid) else {
            return Err(rejected("Readable Campaign Plan page is unavailable"));
        };
        let number: i64 = page.parse().unwrap_or(i64::MAX);
        let characters: Vec<char> = readable.chars().collect();
        let offset = number.saturating_mul(16_000);
        if offset >= characters.len() as i64 {
            return Err(rejected("Readable Campaign Plan page is past the end"));
        }
        let start = offset as usize;
        let content: String = characters[start..(start + 16_000).min(characters.len())].iter().collect();
        let next = start + content.chars().count() < characters.len();
        let selection = object(vec![
            ("kind", "text_page".into()),
            ("offset", offset.into()),
            ("total_characters", (characters.len() as i64).into()),
            ("next_path", if next { format!("readable/page/{}", number + 1).into() } else { Json::Null }),
        ]);
        (content, Some(selection))
    } else if let Some(fragment) = path.strip_prefix("readable#") {
        let readable = readable().ok_or_else(|| rejected("Readable Campaign Plan is unavailable"))?;
        let (heading, content) = select_markdown_section(readable, fragment)?;
        (content, Some(object(vec![("kind", "markdown_section".into()), ("heading", heading.into())])))
    } else {
        return Err(rejected("Campaign Plan reference path is unavailable"));
    };
    if too_large(&content, maximum) {
        return Err(rejected("Campaign Plan reference exceeds the read bound"));
    }
    let mut payload = object(vec![
        ("source", source.clone()),
        ("path", path_value.clone()),
        ("campaign_id", campaign_id.into()),
        ("plan_id", accepted.fields().plan_id.as_str().into()),
        ("content_sha256", accepted.fields().content_sha256.as_str().into()),
        ("content", content.into()),
    ]);
    if let Some(selection) = selection {
        payload.insert("selection".into(), Json::Object(selection));
    }
    payload.insert("visibility".into(), "gm_private_preparation".into());
    payload.insert("precedence".into(), "committed state overrides plan projections".into());
    Ok(payload)
}

/// An excerpt's reply fields: its selection, whether it was cut and the next excerpt's path.
fn excerpt_metadata(excerpt: &TextExcerpt, portable_path: &str) -> JsonMap {
    object(vec![
        ("selection", Json::Object(excerpt.selection.clone())),
        ("truncated", excerpt.truncated.into()),
        (
            "next_path",
            excerpt.next_offset.map_or(Json::Null, |next| format!("{portable_path}#text/{next}").into()),
        ),
    ])
}

fn read_package(
    store: &AuthorityStore,
    campaign_id: &str,
    snapshot: &sylvia_domain::authority::CampaignSnapshot,
    structured: Option<&Json>,
    source: &Json,
    path_value: &Json,
    maximum: i64,
) -> Answer<JsonMap> {
    // `structured.get("bindings")`: only a mapping has one.
    let bindings = match structured {
        Some(Json::Object(structured)) => structured.get("bindings"),
        other => {
            let name = other.map_or("NoneType", py_type_name);
            return Err(uncaught("AttributeError", format!("'{name}' object has no attribute 'get'")));
        }
    };
    // `{...}.get(source)`: a list or object source is unhashable.
    if matches!(source, Json::Array(_) | Json::Object(_)) {
        return Err(uncaught("TypeError", format!("unhashable type: '{}'", py_type_name(source))));
    }
    let binding_name = source.as_str().filter(|name| matches!(*name, "campaign_setting" | "ruleset"));
    let binding = match (binding_name, bindings) {
        (Some(name), Some(Json::Object(bindings))) => bindings.get(name),
        _ => None,
    };
    let Some(Json::Object(binding)) = binding else {
        return Err(rejected("Campaign package reference source is unavailable"));
    };
    let (Some(Json::String(package_id)), Some(Json::String(version)), Some(Json::String(content_sha256))) =
        (binding.get("id"), binding.get("version"), binding.get("content_sha256"))
    else {
        return Err(rejected("Campaign package reference binding is invalid"));
    };
    let lock = &snapshot.state.package_lock;
    let identity = (package_id.as_str(), version.as_str(), content_sha256.as_str());
    let exact = match lock.entries().iter().find(|entry| entry.identity() == identity) {
        Some(entry) => entry.clone(),
        None => match store.compatible_package_binding(campaign_id, identity)? {
            Some(entry) if lock.entries().contains(&entry) => entry,
            _ => return Err(rejected("Campaign package reference binding is stale")),
        },
    };
    let path = text(path_value, "partition")?;
    let (portable_path, separator, fragment) = match path.split_once('#') {
        Some((head, tail)) => (head, true, tail),
        None => (path, false, ""),
    };
    let read = |entry: &PackageLockEntry| {
        store.package_file_bytes(
            entry.package_id(),
            entry.version(),
            entry.sha256(),
            portable_path,
            PACKAGE_MEMBER_BYTE_LIMIT as i64,
        )
    };
    let mut selected = exact.clone();
    let mut value = read(&exact)?;
    if value.is_none() && source.as_str() == Some("ruleset") {
        let dependencies = reference_dependency_identities(&exact);
        if dependencies.len() == 1 {
            if let Some(entry) = lock.entries().iter().find(|entry| identity_of(entry) == dependencies[0]) {
                selected = entry.clone();
                value = read(&selected)?;
            }
        }
    }
    let Some(value) = value else { return Err(rejected("Campaign package reference path is unavailable")) };
    let Ok(original) = String::from_utf8(value) else {
        return Err(rejected("Campaign package reference is not UTF-8"));
    };
    let Some(Json::Object(structured)) = structured else {
        return Err(rejected("Campaign reference requires a structured plan"));
    };
    let is_setting = source.as_str() == Some("campaign_setting");
    let private_references = if is_setting {
        super::content_reads::campaign_setting_knowledge(store, campaign_id, structured)?.1
    } else {
        Vec::new()
    };
    let mut content = original.clone();
    let mut selection: Option<JsonMap> = None;
    let mut selected_heading: Option<String> = None;
    let mut available: Option<(Vec<Json>, i64)> = None;
    let mut excerpt_fields: Option<JsonMap> = None;
    if separator && fragment.starts_with("text/") {
        let digits = &fragment["text/".len()..];
        let valid = digits == "0"
            || (!digits.is_empty()
                && digits.len() <= 10
                && !digits.starts_with('0')
                && digits.chars().all(|character| character.is_ascii_digit()));
        if !valid {
            return Err(rejected_with(
                "Reference text offset is unavailable",
                "campaign_reference_offset_invalid",
            ));
        }
        let excerpt = text_excerpt(&content, digits.parse().unwrap_or(i64::MAX), maximum)?;
        excerpt_fields = Some(excerpt_metadata(&excerpt, portable_path));
        content = excerpt.content;
    } else if separator {
        let (heading, section) = select_markdown_section(&content, fragment)?;
        selection =
            Some(object(vec![("kind", "markdown_section".into()), ("heading", heading.as_str().into())]));
        selected_heading = Some(heading);
        content = section;
    } else if is_setting && portable_path.starts_with("locations/") {
        let overview = markdown_sections(&content).into_iter().find(|(_, slug, _)| slug == "overview");
        if let Some((heading, _, section)) = overview {
            selection = Some(object(vec![
                ("kind", "markdown_section".into()),
                ("heading", heading.as_str().into()),
                ("default", true.into()),
            ]));
            selected_heading = Some(heading);
            content = section;
            let document_private = private_references.iter().any(|item| item == portable_path);
            available = Some(markdown_section_references(&original, portable_path, document_private));
        }
    }
    let budget = maximum.min(REFERENCE_CONTENT_BYTE_LIMIT);
    if excerpt_fields.is_none() && content.len() as i64 > budget {
        if selection.is_some() {
            return Err(rejected_with(
                &format!(
                    "Reference section exceeds the reply bound; read {portable_path}#text/0 or search for a \
                     bounded excerpt"
                ),
                "campaign_reference_too_large",
            ));
        }
        let excerpt = text_excerpt(&content, 0, budget)?;
        excerpt_fields = Some(excerpt_metadata(&excerpt, portable_path));
        content = excerpt.content;
    }
    let private_heading_selected = selected_heading.as_deref().is_some_and(private_heading);
    let visibility = if is_setting
        && (private_references.iter().any(|item| item == portable_path)
            || excerpt_fields.is_some()
            || private_heading_selected)
    {
        "gm_private_preparation"
    } else {
        "campaign_reference"
    };
    let mut payload = object(vec![
        ("source", source.clone()),
        ("path", path_value.clone()),
        ("campaign_id", campaign_id.into()),
        (
            "package",
            Json::Object(object(vec![
                ("id", selected.package_id().into()),
                ("version", selected.version().into()),
                ("content_sha256", selected.sha256().into()),
            ])),
        ),
        ("content", content.into()),
    ]);
    if let Some(fields) = excerpt_fields {
        payload.extend(fields);
    }
    if let Some(selection) = selection {
        payload.insert("selection".into(), Json::Object(selection));
    }
    if let Some((sections, count)) = available.filter(|(_, count)| *count > 0) {
        let truncated = count > sections.len() as i64;
        payload.insert("available_sections".into(), Json::Array(sections));
        payload.insert("available_section_count".into(), count.into());
        payload.insert("available_sections_truncated".into(), truncated.into());
    }
    payload.insert("visibility".into(), visibility.into());
    if is_setting {
        payload.insert(
            "interpretation".into(),
            "Reusable Setting baseline, not current Campaign State. Examples describe what can occur; they do not \
             establish a current object, route, event, or prior action."
                .into(),
        );
    }
    payload.insert(
        "precedence".into(),
        if is_setting {
            "committed state overrides Setting lore"
        } else {
            "MCP mechanics and committed state override Ruleset prose"
        }
        .into(),
    );
    if canonical_json_bytes(&Json::Object(payload.clone())).len() as i64 > REFERENCE_REPLY_BYTE_LIMIT {
        return Err(rejected_with(
            "Reference reply metadata exceeds its byte bound; use an exact bounded text excerpt",
            "campaign_reference_too_large",
        ));
    }
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helpers_follow_python() {
        assert_eq!(title("act two npc2s dé-jà"), "Act Two Npc2S Dé-Jà");
        assert_eq!(slug("  Hooks & Secrets!! "), "hooks-secrets");
        assert_eq!(name_parts("a/b/notes.md"), ("notes", ".md"));
        assert_eq!(name_parts("a/.md"), (".md", ""));
        assert_eq!(name_parts("a/b."), ("b.", ""));
        assert!(private_heading("SECRETS"));
        let sections = markdown_sections("# A\nx\n## B\ny\n# C\n#  \n#\tz");
        let headings: Vec<&str> = sections.iter().map(|(heading, _, _)| heading.as_str()).collect();
        assert_eq!(headings, ["A", "B", "C", "", "z"]);
        assert_eq!(sections[0].2, "# A\nx\n## B\ny");
    }
}
