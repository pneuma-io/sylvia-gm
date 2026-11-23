//! `application/reusable_plans.py`: a small immutable local library for explicitly reviewed
//! reusable Campaign Plans.
//!
//! Python's validators are callables taking `(payload, readable_markdown)`; here they are
//! [`PlanValidator`] closures. Where Python falls back to the bundled Rift validator, the caller
//! passes the [`BundledPackages`] it reads from.

use crate::bundled_packages::BundledPackages;
use crate::plan_snapshot::{validate_plan_snapshot, PLAN_SNAPSHOT_BYTE_LIMIT, PLAN_SNAPSHOT_FILENAME};
use crate::reusable_library_deletion::{
    delete_exact_library_entry, validate_owned_files, validated_library_entry,
};
use crate::workspace_files::os_error;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, pyre, pystr, CodedError, Json, JsonMap};
use sylvia_domain::campaign_plan::{validate_campaign_plan, CampaignPlanExpectations, ValidatedCampaignPlan};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::interview_contribution::parse_interview_contribution_bytes;
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::{py_eq, py_str};

pub const REUSABLE_PLAN_FORMAT_ID: &str = "sylvia-gm-reusable-campaign-plan";
pub const REUSABLE_PLAN_FORMAT_VERSION: i64 = 3;
const LEGACY_REUSABLE_PLAN_FORMAT_VERSION: i64 = 2;
const MAX_LIBRARY_PLANS: usize = 256;
const RIFT_SETUP_ID: &str = "the-rift-awakening";
const LIBRARY_NAME: &str = "campaign-plans";
const OWNED_NAMES: [&str; 5] =
    ["plan.json", "plan.md", "interview-contribution.json", "record.json", PLAN_SNAPSHOT_FILENAME];
const COMMON_FIELDS: [&str; 8] = [
    "format_id",
    "format_version",
    "artifact_id",
    "content_sha256",
    "plan_content_sha256",
    "title",
    "summary",
    "source_plan_content_sha256",
];

/// `_LIBRARY_WRITE_LOCK`.
static LIBRARY_WRITE_LOCK: Mutex<()> = Mutex::new(());

/// `CampaignPlanValidator`: `(payload, readable_markdown) -> ValidatedCampaignPlan`.
pub type PlanValidator<'a> = &'a dyn Fn(&Json, &str) -> DomainResult<ValidatedCampaignPlan>;

/// `on_invalid(name, error)`: told of each catalog entry that fails to load.
pub type InvalidHandler<'a> = &'a mut dyn FnMut(&str, &DomainError);

/// `ReusableCampaignPlan`: one exact immutable reusable sibling and its player-safe catalog record.
#[derive(Debug, Clone, PartialEq)]
pub struct ReusableCampaignPlan {
    pub artifact_id: String,
    pub title: String,
    pub summary: String,
    pub content_sha256: String,
    pub plan: ValidatedCampaignPlan,
    pub interview_contribution: Option<JsonMap>,
    pub setup_id: Option<String>,
    pub package_snapshot: Option<Vec<u8>>,
}

fn canonical_value(value: &Json) -> Json {
    let text = String::from_utf8(canonical_json_bytes(value)).expect("canonical JSON is UTF-8");
    pyjson::loads(&text).expect("canonical JSON reads back")
}

impl ReusableCampaignPlan {
    /// `catalog_payload()`.
    pub fn catalog_payload(&self) -> JsonMap {
        let bindings = &self.plan.payload["bindings"];
        let mut payload = JsonMap::new();
        payload.insert("kind".into(), Json::from("campaign_plan"));
        payload.insert("id".into(), Json::from(self.artifact_id.as_str()));
        payload.insert("title".into(), Json::from(self.title.as_str()));
        payload.insert("summary".into(), Json::from(self.summary.as_str()));
        payload.insert("content_sha256".into(), Json::from(self.content_sha256.as_str()));
        payload.insert("reusable".into(), Json::Bool(true));
        payload.insert("ruleset".into(), bindings["ruleset"].clone());
        payload.insert("campaign_setting".into(), bindings["campaign_setting"].clone());
        payload.insert("starting_party".into(), canonical_value(&self.plan.payload["starting_party"]));
        // The Interview resolves questions from the selected saved content on demand.
        payload.insert("interview_contribution".into(), Json::Null);
        if let Some(setup_id) = &self.setup_id {
            payload.insert("setup_id".into(), Json::from(setup_id.as_str()));
        }
        payload
    }
}

fn library_root(storage_root: &Path) -> PathBuf {
    storage_root.join(LIBRARY_NAME)
}

pub(crate) fn json_error(error: CodedError) -> DomainError {
    match error.error_type {
        Some(class @ ("JSONDecodeError" | "UnicodeDecodeError")) => {
            DomainError::Diagnosed { error_type: class, message: error.message, diagnostics: Vec::new() }
        }
        _ => DomainError::value(error.message),
    }
}

pub(crate) fn unicode_error(message: String) -> DomainError {
    DomainError::Diagnosed { error_type: "UnicodeDecodeError", message, diagnostics: Vec::new() }
}

pub(crate) fn read_bytes(path: &Path) -> DomainResult<Vec<u8>> {
    std::fs::read(path).map_err(|error| os_error(&error, path))
}

/// `path.read_text(encoding="utf-8")`: a strict decode with universal newlines.
pub(crate) fn read_text(path: &Path) -> DomainResult<String> {
    let text = pystr::utf8_decode(&read_bytes(path)?).map_err(unicode_error)?;
    Ok(text.replace("\r\n", "\n").replace('\r', "\n"))
}

/// `json.loads(text)`.
pub(crate) fn loads(text: &str) -> DomainResult<Json> {
    pyjson::loads(text).map_err(json_error)
}

/// `json.loads(data)` for bytes.
fn loads_bytes(data: &[u8]) -> DomainResult<Json> {
    pyjson::loads_bytes(data).map_err(json_error)
}

/// `re.fullmatch(r"[0-9a-f]{N}", text)`.
pub(crate) fn lower_hex(text: &str, length: usize) -> bool {
    text.len() == length && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

/// `re.fullmatch(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}", artifact_id)`.
fn is_artifact_id(artifact_id: &str) -> bool {
    artifact_id
        .strip_prefix("campaign-plan-reusable-")
        .and_then(|rest| rest.split_once('-'))
        .is_some_and(|(operation, content)| lower_hex(operation, 24) && lower_hex(content, 16))
}

/// `_strip_section(markdown, heading)`: `re.sub(r"\n+## {heading}\s*\n.*?(?=\n## |\Z)", "",
/// markdown, flags=re.DOTALL).rstrip() + "\n"`.
fn strip_section(markdown: &str, heading: &str) -> String {
    let marker = format!("## {heading}");
    let mut out = String::new();
    let (mut last, mut search) = (0, 0);
    while let Some(offset) = markdown[search..].find('\n') {
        let start = search + offset;
        let run_end = start + markdown[start..].bytes().take_while(|byte| *byte == b'\n').count();
        let matched = markdown[run_end..].strip_prefix(&marker).and_then(|_| {
            let heading_end = run_end + marker.len();
            // `\s*\n`: greedy whitespace, giving back to its last newline.
            let newline = markdown[heading_end..]
                .char_indices()
                .take_while(|(_, c)| pystr::is_space(*c))
                .filter(|(_, c)| *c == '\n')
                .last()
                .map(|(index, _)| heading_end + index)?;
            let body = newline + 1;
            Some(markdown[body..].find("\n## ").map_or(markdown.len(), |index| body + index))
        });
        match matched {
            Some(end) => {
                out.push_str(&markdown[last..start]);
                last = end;
                search = end;
            }
            // A later start inside the same newline run reaches the same text and fails too.
            None => search = run_end,
        }
        if search >= markdown.len() {
            break;
        }
    }
    out.push_str(&markdown[last..]);
    format!("{}\n", pystr::rstrip(&out))
}

/// `re.sub(r"^# .*?$", f"# {title}", markdown, count=1, flags=re.MULTILINE)`. The template is
/// parsed first, so a bad one fails even without a heading line.
fn retitle(markdown: &str, title: &str) -> DomainResult<String> {
    let pieces = pyre::parse_template(&format!("# {title}"), 0)
        .map_err(|error| DomainError::uncaught(error.class, error.message))?;
    let mut line_start = 0;
    loop {
        let line_end = markdown[line_start..].find('\n').map_or(markdown.len(), |index| line_start + index);
        let line = &markdown[line_start..line_end];
        if line.starts_with("# ") {
            let replaced = pyre::expand(&pieces, &[line]);
            return Ok(format!("{}{replaced}{}", &markdown[..line_start], &markdown[line_end..]));
        }
        if line_end == markdown.len() {
            return Ok(markdown.to_owned());
        }
        line_start = line_end + 1;
    }
}

const GUIDANCE_PREFIXES: [&str; 5] = [
    "Player Preferences:",
    "Player Specific Requests:",
    "Accepted ruleset contribution:",
    "Accepted setting contribution:",
    "Accepted plan contribution:",
];

/// `_generalized_rift_payload(payload, title=..., plan_id=...)`: remove the known current-party
/// and Interview overlays from one Rift-derived Plan.
fn generalized_rift_payload(payload: &JsonMap, title: &str, plan_id: &str) -> DomainResult<JsonMap> {
    let Json::Object(mut value) = canonical_value(&Json::Object(payload.clone())) else {
        return Err(DomainError::value("Campaign Plan payload is invalid"));
    };
    value.insert("id".into(), Json::from(plan_id));
    value.insert("revision".into(), Json::from(1));
    value.insert("title".into(), Json::from(title));
    // Authored party policy and world state belong to this Plan; live party choices are separate.
    if let Some(Json::Array(boundaries)) = value.get_mut("boundaries") {
        boundaries.retain(|item| !item.as_str().is_some_and(|text| text.starts_with("Player Boundary:")));
    }
    if let Some(Json::Object(guidance)) = value.get_mut("gm_guidance") {
        if let Some(Json::String(instructions)) = guidance.get("instructions") {
            let kept: Vec<&str> = pystr::splitlines(instructions)
                .into_iter()
                .filter(|line| !GUIDANCE_PREFIXES.iter().any(|prefix| line.starts_with(prefix)))
                .collect();
            let joined = pystr::strip(&kept.join("\n")).to_owned();
            guidance.insert("instructions".into(), Json::from(joined));
        }
    }
    Ok(value)
}

static TEMPORARY_NAMES: AtomicU64 = AtomicU64::new(0);

/// A fresh name in the style of `tempfile`'s, unique within this process.
pub(crate) fn temporary_name(prefix: &str, suffix: &str) -> String {
    let count = TEMPORARY_NAMES.fetch_add(1, Ordering::Relaxed);
    let nanos =
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.subsec_nanos());
    format!("{prefix}{}{nanos:x}{count:x}{suffix}", std::process::id())
}

/// `_atomic_write(path, value)`: a fsynced temporary file renamed over `path`.
pub(crate) fn atomic_write(path: &Path, value: &[u8]) -> DomainResult<()> {
    let parent = path.parent().expect("a file in a directory");
    std::fs::create_dir_all(parent).map_err(|error| os_error(&error, parent))?;
    let name = path.file_name().expect("a file name").to_string_lossy();
    let temporary = parent.join(temporary_name(&format!(".{name}-"), ".tmp"));
    let written = (|| {
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
        file.write_all(value)?;
        file.flush()?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&temporary, path)
    })();
    written.map_err(|error| {
        let _ = std::fs::remove_file(&temporary);
        os_error(&error, path)
    })
}

/// `_validate_rift_plan(payload, readable_markdown)`: validate against the bundled Rift package
/// closure, or the exact retained Rift snapshot the Plan pins.
pub fn validate_rift_plan(
    bundled: &BundledPackages,
    payload: &Json,
    readable_markdown: &str,
) -> DomainResult<ValidatedCampaignPlan> {
    let authority = bundled.bundled_rift_authority()?;
    let closure = payload
        .as_object()
        .and_then(|map| map.get("bindings"))
        .and_then(Json::as_object)
        .and_then(|bindings| bindings.get("package_closure_sha256"))
        .unwrap_or(&Json::Null);
    if !py_eq(closure, &Json::from(authority.closure_sha256.as_str())) {
        if let Some(snapshot) = bundled.bundled_rift_archive_for_closure(closure)? {
            return validate_plan_snapshot(&snapshot, payload, readable_markdown);
        }
    }
    validate_campaign_plan(
        payload,
        readable_markdown,
        CampaignPlanExpectations {
            expected_ruleset: Some(&authority.ruleset),
            expected_setting: Some(&authority.setting),
            expected_closure_sha256: &authority.closure_sha256,
            setting_paths: &authority.setting_paths,
            setting_knowledge_ids: &authority.setting_knowledge_ids,
        },
    )
}

fn content_hash(validated: &ValidatedCampaignPlan, contribution: &[u8], snapshot: Option<&[u8]>) -> String {
    let mut data = canonical_json_bytes(&Json::Object(validated.payload.clone()));
    data.extend_from_slice(validated.readable_markdown.as_bytes());
    data.extend_from_slice(contribution);
    data.extend_from_slice(snapshot.unwrap_or_default());
    sha256_hex(&data)
}

fn operation_suffix(operation_id: &str) -> String {
    sha256_hex(operation_id.as_bytes())[..24].to_owned()
}

/// The pieces of one save that Python passes as keyword arguments.
struct Save<'a> {
    operation_id: &'a str,
    source_payload: &'a Json,
    validated: ValidatedCampaignPlan,
    validate_plan: PlanValidator<'a>,
    setup_id: Option<&'a str>,
    contribution_bytes: Vec<u8>,
    package_snapshot: Option<&'a [u8]>,
}

/// `_save_validated_campaign_plan(...)`: persist one validated immutable Plan with explicit
/// catalog routing metadata.
fn save_validated(
    storage_root: &Path,
    bundled: &BundledPackages,
    save: Save<'_>,
) -> DomainResult<ReusableCampaignPlan> {
    let Save {
        operation_id,
        source_payload,
        validated,
        validate_plan,
        setup_id,
        contribution_bytes,
        package_snapshot,
    } = save;
    if let Some(snapshot) = package_snapshot {
        validate_plan_snapshot(
            snapshot,
            &Json::Object(validated.payload.clone()),
            &validated.readable_markdown,
        )?;
    }
    let content_sha256 = content_hash(&validated, &contribution_bytes, package_snapshot);
    let artifact_id =
        format!("campaign-plan-reusable-{}-{}", operation_suffix(operation_id), &content_sha256[..16]);
    let target = library_root(storage_root).join(&artifact_id);
    let mut record = JsonMap::new();
    record.insert("format_id".into(), Json::from(REUSABLE_PLAN_FORMAT_ID));
    let format_version = if package_snapshot.is_none() {
        LEGACY_REUSABLE_PLAN_FORMAT_VERSION
    } else {
        REUSABLE_PLAN_FORMAT_VERSION
    };
    record.insert("format_version".into(), Json::from(format_version));
    record.insert("artifact_id".into(), Json::from(artifact_id.as_str()));
    record.insert("content_sha256".into(), Json::from(content_sha256.as_str()));
    record.insert("plan_content_sha256".into(), Json::from(validated.content_sha256.as_str()));
    record.insert("title".into(), Json::from(validated.title.as_str()));
    record.insert("summary".into(), Json::from(py_str(&validated.payload["premise"])));
    record.insert(
        "source_plan_content_sha256".into(),
        Json::from(sha256_hex(canonical_json_bytes(source_payload))),
    );
    record.insert("setup_id".into(), setup_id.map_or(Json::Null, Json::from));
    if let Some(snapshot) = package_snapshot {
        record.insert("package_snapshot".into(), Json::from(PLAN_SNAPSHOT_FILENAME));
        record.insert("package_snapshot_sha256".into(), Json::from(sha256_hex(snapshot)));
    }
    let _guard = LIBRARY_WRITE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    if target.exists() {
        let existing = load_reusable_campaign_plan(storage_root, &artifact_id, Some(validate_plan), bundled)?;
        if existing.content_sha256 != content_sha256 {
            return Err(DomainError::value("Reusable Campaign Plan identity conflicts"));
        }
        return Ok(existing);
    }
    let parent = target.parent().expect("inside the library");
    std::fs::create_dir_all(parent).map_err(|error| os_error(&error, parent))?;
    // Catalog readers see either no entry or the complete immutable artifact.
    let temporary = storage_root.join(temporary_name(".plan-save-", ""));
    std::fs::create_dir(&temporary).map_err(|error| os_error(&error, &temporary))?;
    let staged = temporary.join(&artifact_id);
    let written = (|| {
        std::fs::create_dir(&staged).map_err(|error| os_error(&error, &staged))?;
        atomic_write(
            &staged.join("plan.json"),
            &canonical_json_bytes(&Json::Object(validated.payload.clone())),
        )?;
        atomic_write(&staged.join("plan.md"), validated.readable_markdown.as_bytes())?;
        if !contribution_bytes.is_empty() {
            atomic_write(&staged.join("interview-contribution.json"), &contribution_bytes)?;
        }
        if let Some(snapshot) = package_snapshot {
            atomic_write(&staged.join(PLAN_SNAPSHOT_FILENAME), snapshot)?;
        }
        atomic_write(&staged.join("record.json"), &canonical_json_bytes(&Json::Object(record)))?;
        std::fs::rename(&staged, &target).map_err(|error| os_error(&error, &staged))
    })();
    let cleaned = std::fs::remove_dir_all(&temporary).map_err(|error| os_error(&error, &temporary));
    written?;
    cleaned?;
    load_reusable_campaign_plan(storage_root, &artifact_id, Some(validate_plan), bundled)
}

/// `save_reusable_campaign_plan(...)`: persist an exact Plan admitted against its retained
/// package context.
#[allow(clippy::too_many_arguments)]
pub fn save_reusable_campaign_plan(
    storage_root: &Path,
    bundled: &BundledPackages,
    operation_id: &str,
    source_payload: &Json,
    source_markdown: &str,
    validate_plan: PlanValidator<'_>,
    package_snapshot: Option<&[u8]>,
    setup_id: Option<&str>,
    interview_contribution: Option<&JsonMap>,
) -> DomainResult<ReusableCampaignPlan> {
    let validated = validate_plan(source_payload, source_markdown)?;
    let contribution_bytes = match interview_contribution {
        None => Vec::new(),
        Some(contribution) => {
            let parsed = parse_interview_contribution_bytes(
                &canonical_json_bytes(&Json::Object(contribution.clone())),
                "campaign_plan",
            )?;
            canonical_json_bytes(&Json::Object(parsed.as_payload()))
        }
    };
    save_validated(
        storage_root,
        bundled,
        Save {
            operation_id,
            source_payload,
            validated,
            validate_plan,
            setup_id,
            contribution_bytes,
            package_snapshot,
        },
    )
}

/// `save_reusable_rift_plan(...)`: create one generalized immutable sibling from a validated
/// Rift-derived Plan.
pub fn save_reusable_rift_plan(
    storage_root: &Path,
    bundled: &BundledPackages,
    operation_id: &str,
    title: &str,
    source_payload: &JsonMap,
    source_markdown: &str,
    package_snapshot: Option<&[u8]>,
) -> DomainResult<ReusableCampaignPlan> {
    if title != pystr::strip(title) || title.is_empty() || pystr::len(title) > 160 || title.contains('\0') {
        return Err(DomainError::value("Reusable Campaign Plan title is invalid"));
    }
    let plan_id = format!("reusable-{}", operation_suffix(operation_id));
    let payload = generalized_rift_payload(source_payload, title, &plan_id)?;
    let mut markdown = source_markdown.replace("\r\n", "\n").replace('\r', "\n");
    for heading in ["Selected campaign party", "Player customization brief", "Accepted Campaign Construction"]
    {
        markdown = strip_section(&markdown, heading);
    }
    if pystr::lstrip(&markdown).starts_with("# ") {
        markdown = retitle(&markdown, title)?;
    }
    let rift = |value: &Json, text: &str| validate_rift_plan(bundled, value, text);
    let snapshot =
        |value: &Json, text: &str| validate_plan_snapshot(package_snapshot.unwrap_or_default(), value, text);
    let validator: PlanValidator<'_> = if package_snapshot.is_none() { &rift } else { &snapshot };
    let validated = validator(&Json::Object(payload), &markdown)?;
    let contribution_bytes = match bundled.bundled_rift_interview_contribution()? {
        None => Vec::new(),
        Some(contribution) => canonical_json_bytes(&Json::Object(contribution.as_payload())),
    };
    save_validated(
        storage_root,
        bundled,
        Save {
            operation_id,
            source_payload: &Json::Object(source_payload.clone()),
            validated,
            validate_plan: validator,
            setup_id: Some(RIFT_SETUP_ID),
            contribution_bytes,
            package_snapshot,
        },
    )
}

fn invalid(message: &str) -> DomainError {
    DomainError::value(message)
}

/// The `setup_id` of a format 2 or 3 record.
fn record_setup_id(record: &JsonMap) -> DomainResult<Option<String>> {
    match &record["setup_id"] {
        Json::Null => Ok(None),
        Json::String(text) if !text.is_empty() && !text.contains('\0') => Ok(Some(text.clone())),
        _ => Err(invalid("Reusable Campaign Plan setup identity is invalid")),
    }
}

fn has_fields(record: &JsonMap, extra: &[&str]) -> bool {
    record.len() == COMMON_FIELDS.len() + extra.len()
        && COMMON_FIELDS.iter().chain(extra).all(|field| record.contains_key(*field))
}

/// `load_reusable_campaign_plan(storage_root, artifact_id, validate_plan=...)`: load and
/// revalidate one exact local reusable Plan without trusting directory contents.
pub fn load_reusable_campaign_plan(
    storage_root: &Path,
    artifact_id: &str,
    validate_plan: Option<PlanValidator<'_>>,
    bundled: &BundledPackages,
) -> DomainResult<ReusableCampaignPlan> {
    if !is_artifact_id(artifact_id) {
        return Err(invalid("Reusable Campaign Plan identity is invalid"));
    }
    let Some(root) = validated_library_entry(storage_root, LIBRARY_NAME, artifact_id)? else {
        return Err(DomainError::uncaught("FileNotFoundError", artifact_id));
    };
    validate_owned_files(&root, &OWNED_NAMES)?;
    let Json::Object(record) = loads(&read_text(&root.join("record.json"))?)? else {
        return Err(invalid("Reusable Campaign Plan record is invalid"));
    };
    let format_version = record.get("format_version").cloned().unwrap_or(Json::Null);
    let version = |value: i64| py_eq(&format_version, &Json::from(value));
    let setup_id = if version(1) {
        if !has_fields(&record, &[]) {
            return Err(invalid("Reusable Campaign Plan record is invalid"));
        }
        Some(RIFT_SETUP_ID.to_owned())
    } else if version(LEGACY_REUSABLE_PLAN_FORMAT_VERSION) {
        if !has_fields(&record, &["setup_id"]) {
            return Err(invalid("Reusable Campaign Plan record is invalid"));
        }
        record_setup_id(&record)?
    } else if version(REUSABLE_PLAN_FORMAT_VERSION) {
        if !has_fields(&record, &["setup_id", "package_snapshot", "package_snapshot_sha256"]) {
            return Err(invalid("Reusable Campaign Plan record is invalid"));
        }
        let setup_id = record_setup_id(&record)?;
        if !py_eq(&record["package_snapshot"], &Json::from(PLAN_SNAPSHOT_FILENAME)) {
            return Err(invalid("Reusable Campaign Plan snapshot file is invalid"));
        }
        if !record["package_snapshot_sha256"].as_str().is_some_and(|text| lower_hex(text, 64)) {
            return Err(invalid("Reusable Campaign Plan snapshot hash is invalid"));
        }
        setup_id
    } else {
        return Err(invalid("Reusable Campaign Plan record is unsupported"));
    };
    if !py_eq(&record["format_id"], &Json::from(REUSABLE_PLAN_FORMAT_ID))
        || !py_eq(&record["artifact_id"], &Json::from(artifact_id))
    {
        return Err(invalid("Reusable Campaign Plan record is unsupported"));
    }
    let plan_bytes = read_bytes(&root.join("plan.json"))?;
    let markdown = pystr::utf8_decode(&read_bytes(&root.join("plan.md"))?).map_err(unicode_error)?;
    let contribution_path = root.join("interview-contribution.json");
    let has_contribution = contribution_path.exists();
    let contribution_bytes = if has_contribution { read_bytes(&contribution_path)? } else { Vec::new() };
    let mut actual_names = Vec::new();
    for entry in std::fs::read_dir(&root).map_err(|error| os_error(&error, &root))? {
        let entry = entry.map_err(|error| os_error(&error, &root))?;
        actual_names.push(entry.file_name().to_string_lossy().into_owned());
    }
    actual_names.sort();
    let mut required_names = vec!["plan.json", "plan.md", "record.json"];
    if has_contribution {
        required_names.push("interview-contribution.json");
    }
    let mut package_snapshot = None;
    let is_current = version(REUSABLE_PLAN_FORMAT_VERSION);
    if is_current {
        required_names.push(PLAN_SNAPSHOT_FILENAME);
    }
    required_names.sort_unstable();
    if actual_names != required_names {
        return Err(invalid("Reusable Campaign Plan files do not match its record"));
    }
    let validated = if is_current {
        let path = root.join(PLAN_SNAPSHOT_FILENAME);
        let mut snapshot = Vec::new();
        std::fs::File::open(&path)
            .and_then(|file| file.take(PLAN_SNAPSHOT_BYTE_LIMIT as u64 + 1).read_to_end(&mut snapshot))
            .map_err(|error| os_error(&error, &path))?;
        if snapshot.len() > PLAN_SNAPSHOT_BYTE_LIMIT {
            return Err(invalid("Reusable Campaign Plan snapshot is too large"));
        }
        if !py_eq(&Json::from(sha256_hex(&snapshot)), &record["package_snapshot_sha256"]) {
            return Err(invalid("Reusable Campaign Plan snapshot hash is invalid"));
        }
        let contained = validate_plan_snapshot(&snapshot, &loads_bytes(&plan_bytes)?, &markdown)?;
        let validated = match validate_plan {
            None => contained,
            Some(validate_plan) => validate_plan(&loads_bytes(&plan_bytes)?, &markdown)?,
        };
        package_snapshot = Some(snapshot);
        validated
    } else {
        let payload = loads_bytes(&plan_bytes)?;
        match validate_plan {
            None => validate_rift_plan(bundled, &payload, &markdown)?,
            Some(validate_plan) => validate_plan(&payload, &markdown)?,
        }
    };
    if !py_eq(&Json::from(validated.content_sha256.as_str()), &record["plan_content_sha256"]) {
        return Err(invalid("Reusable Campaign Plan structured hash is invalid"));
    }
    if !py_eq(&record["title"], &Json::from(validated.title.as_str()))
        || !py_eq(&record["summary"], &validated.payload["premise"])
    {
        return Err(invalid("Reusable Campaign Plan catalog metadata does not match its content"));
    }
    if let Some(setup_id) = &setup_id {
        if setup_id != RIFT_SETUP_ID {
            return Err(invalid("Reusable Campaign Plan setup is unsupported"));
        }
        let bindings = &validated.payload["bindings"];
        if !py_eq(&bindings["ruleset"]["id"], &Json::from("sylvia.srd-lite.bg3"))
            || !py_eq(&bindings["campaign_setting"]["id"], &Json::from("pneuma.severed-lands"))
        {
            return Err(invalid("The Rift setup requires its own Ruleset and Setting"));
        }
    }
    let contribution = if contribution_bytes.is_empty() {
        None
    } else {
        Some(parse_interview_contribution_bytes(&contribution_bytes, "campaign_plan")?.as_payload())
    };
    let contribution_canonical = contribution
        .as_ref()
        .map(|payload| canonical_json_bytes(&Json::Object(payload.clone())))
        .unwrap_or_default();
    let content_sha256 = content_hash(&validated, &contribution_canonical, package_snapshot.as_deref());
    if !py_eq(&Json::from(content_sha256.as_str()), &record["content_sha256"]) {
        return Err(invalid("Reusable Campaign Plan content hash is invalid"));
    }
    Ok(ReusableCampaignPlan {
        artifact_id: artifact_id.to_owned(),
        title: py_str(&record["title"]),
        summary: py_str(&record["summary"]),
        content_sha256,
        plan: validated,
        interview_contribution: contribution,
        setup_id,
        package_snapshot,
    })
}

/// The classes `reusable_campaign_plan_catalog` catches: `OSError`, `UnicodeError`, `ValueError`
/// and `CommandRejected`, with their subclasses.
pub(crate) fn catalog_catches(error: &DomainError) -> bool {
    const CAUGHT: [&str; 13] = [
        "OSError",
        "FileNotFoundError",
        "FileExistsError",
        "PermissionError",
        "IsADirectoryError",
        "NotADirectoryError",
        "BlockingIOError",
        "InterruptedError",
        "TimeoutError",
        "UnicodeError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "CommandRejected",
    ];
    error.is_value_error() || CAUGHT.contains(&error.python_class())
}

/// `reusable_campaign_plan_catalog(storage_root, validate_plan=..., on_invalid=...)`: list valid
/// Plans, reporting each rejected entry when discovery supplies a handler.
pub fn reusable_campaign_plan_catalog(
    storage_root: &Path,
    validate_plan: Option<PlanValidator<'_>>,
    mut on_invalid: Option<InvalidHandler<'_>>,
    bundled: &BundledPackages,
) -> DomainResult<Vec<ReusableCampaignPlan>> {
    let root = library_root(storage_root);
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut directories = Vec::new();
    for entry in std::fs::read_dir(&root).map_err(|error| os_error(&error, &root))? {
        let path = entry.map_err(|error| os_error(&error, &root))?.path();
        if path.is_dir() {
            directories.push(path);
        }
    }
    if directories.len() > MAX_LIBRARY_PLANS {
        return Err(invalid("Reusable Campaign Plan library is oversized"));
    }
    let mut names: Vec<String> = directories
        .iter()
        .map(|path| path.file_name().expect("an entry").to_string_lossy().into_owned())
        .collect();
    names.sort();
    let mut plans = Vec::new();
    for name in names {
        match load_reusable_campaign_plan(storage_root, &name, validate_plan, bundled) {
            Ok(plan) => {
                // A catalog retains metadata, not another archive copy for every saved Plan.
                plans.push(ReusableCampaignPlan { package_snapshot: None, ..plan });
            }
            Err(error) if catalog_catches(&error) => match on_invalid.as_mut() {
                Some(handler) => handler(&name, &error),
                None => return Err(error),
            },
            Err(error) => return Err(error),
        }
    }
    Ok(plans)
}

/// `delete_reusable_campaign_plan(storage_root, artifact_id, expected_content_sha256=...,
/// confirmation=...)`: delete one exact local Plan after fencing against a stale catalog choice.
pub fn delete_reusable_campaign_plan(
    storage_root: &Path,
    artifact_id: &str,
    expected_content_sha256: &str,
    confirmation: &str,
) -> DomainResult<bool> {
    if !is_artifact_id(artifact_id) {
        return Err(invalid("Reusable Campaign Plan identity is invalid"));
    }
    if !lower_hex(expected_content_sha256, 64) {
        return Err(invalid("Reusable Campaign Plan version is invalid"));
    }
    if confirmation != format!("permanently delete {artifact_id}") {
        return Err(invalid("Reusable Campaign Plan deletion confirmation is not exact"));
    }
    let _guard = LIBRARY_WRITE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let Some(target) = validated_library_entry(storage_root, LIBRARY_NAME, artifact_id)? else {
        return Ok(false);
    };
    validate_owned_files(&target, &OWNED_NAMES)?;
    let record = loads(&read_text(&target.join("record.json"))?)?;
    let Json::Object(record) = record else {
        return Err(invalid("Reusable Campaign Plan record is invalid"));
    };
    if !record.get("artifact_id").is_some_and(|value| py_eq(value, &Json::from(artifact_id))) {
        return Err(invalid("Reusable Campaign Plan record is invalid"));
    }
    if !record.get("content_sha256").is_some_and(|value| py_eq(value, &Json::from(expected_content_sha256))) {
        return Err(invalid("Reusable Campaign Plan changed; refresh the library and try again"));
    }
    delete_exact_library_entry(storage_root, LIBRARY_NAME, artifact_id, &OWNED_NAMES)
}

#[cfg(test)]
mod tests {
    use super::{retitle, strip_section};
    use serde_json::json;
    use sylvia_base::pyjson;

    /// `_strip_section` and the retitling `re.sub` on the generator's random renderings.
    #[test]
    fn text_helpers_match_python() {
        let vectors = pyjson::loads(include_str!("../tests/vectors/reusable_plan_vectors.json")).unwrap();
        let cases = vectors["texts"].as_array().unwrap();
        let mut failures = Vec::new();
        for case in cases {
            let markdown = case["markdown"].as_str().unwrap();
            let stripped = strip_section(markdown, case["heading"].as_str().unwrap());
            if stripped != case["stripped"].as_str().unwrap() {
                failures.push(format!("strip {markdown:?}: python {}, rust {stripped:?}", case["stripped"]));
            }
            let retitled = match retitle(markdown, case["title"].as_str().unwrap()) {
                Ok(text) => json!(["ok", text]),
                Err(error) => json!([error.python_class(), error.message()]),
            };
            if retitled != case["retitled"] {
                failures.push(format!("retitle {markdown:?}: python {}, rust {retitled}", case["retitled"]));
            }
        }
        assert!(
            failures.is_empty(),
            "{} of {} differ: {:#?}",
            failures.len(),
            cases.len() * 2,
            &failures[..failures.len().min(6)]
        );
    }
}
