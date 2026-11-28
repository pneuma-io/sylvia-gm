//! `application/character_profiles.py`: validate and retain immutable, setting-independent
//! character sources.
//!
//! Reading follows `json.loads(..., object_pairs_hook=<refuse duplicates>,
//! parse_constant=<refuse NaN/Infinity>)` through [`pyjson::loads_hooked`], then Python's
//! depth-and-finiteness walk in its own order. Nesting of 128 levels or more is refused by the
//! declared limit (`docs/rust-port/differences.md` row 4) with the walk's own depth correction;
//! an integer beyond 64 bits or a lone surrogate (rows 1 and 3) with the malformed-JSON one.

use crate::reusable_library_deletion::{
    is_link_or_reparse, lexists, validate_owned_files, validated_library_entry,
};
use crate::workspace_files::os_error;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{loads_hooked, HookFailure, HookPolicy};
use sylvia_base::pystr;
use sylvia_base::pyurl::urlsplit;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::content::{validate_content_shape, ContentShapeError};
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::canonical_json_bytes;

/// `MAX_PROFILE_BYTES`.
pub const MAX_PROFILE_BYTES: usize = 256 * 1024;
const LIBRARY: &str = "character-profiles";
const FILES: [&str; 2] = ["profile.json", "record.json"];
const RECORD_FORMAT_ID: &str = "sylvia-gm-character-profile-record";
/// `_LOCK`: one save at a time in the owner process.
static LOCK: Mutex<()> = Mutex::new(());

fn lower_hex(text: &str, length: usize) -> bool {
    text.len() == length && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

/// `PROFILE_ID.fullmatch(text)`: `character-profile-[0-9a-f]{24}-[0-9a-f]{16}`.
pub fn is_profile_id(text: &str) -> bool {
    text.strip_prefix("character-profile-")
        .and_then(|rest| rest.split_once('-'))
        .is_some_and(|(operation, digest)| lower_hex(operation, 24) && lower_hex(digest, 16))
}

/// `_SHA256.fullmatch(text)`.
fn is_sha256(text: &str) -> bool {
    lower_hex(text, 64)
}

/// `_profile_error(code, message, path)`: Builder's procedural corrections through the shared
/// diagnostic channel.
fn profile_error(code: &str, message: &str, path: &str) -> DomainError {
    ContentShapeError::new(vec![RulesetDiagnostic {
        code: code.into(),
        phase: "content".into(),
        message: message.into(),
        file: Some("character-profile.json".into()),
        path: Some(path.into()),
        ..Default::default()
    }])
    .into()
}

fn depth_error() -> DomainError {
    profile_error("profile_depth_invalid", "Use at most 32 nesting levels.", "$")
}

/// The shape of a read document for the walk: containers in member order, and where a number
/// overflowed to infinity (`pyjson` holds `null` there).
enum Shape {
    Container(Vec<Shape>),
    Infinite,
    Other,
}

/// Re-scan text `loads_hooked` has already read (so it is valid JSON, nested under 128 levels)
/// for its structure and its overflowing numbers.
fn shape(text: &[u8], index: &mut usize) -> Shape {
    let skip = |index: &mut usize| {
        while text.get(*index).is_some_and(|byte| matches!(byte, b' ' | b'\t' | b'\n' | b'\r')) {
            *index += 1;
        }
    };
    skip(index);
    match text[*index] {
        open @ (b'{' | b'[') => {
            let close = if open == b'{' { b'}' } else { b']' };
            *index += 1;
            let mut children = Vec::new();
            loop {
                skip(index);
                if text[*index] == close {
                    *index += 1;
                    return Shape::Container(children);
                }
                if text[*index] == b',' {
                    *index += 1;
                    continue;
                }
                if open == b'{' {
                    let _key = shape(text, index);
                    skip(index);
                    *index += 1; // ':'
                }
                children.push(shape(text, index));
            }
        }
        b'"' => {
            *index += 1;
            while text[*index] != b'"' {
                *index += if text[*index] == b'\\' { 2 } else { 1 };
            }
            *index += 1;
            Shape::Other
        }
        b'-' | b'0'..=b'9' => {
            let start = *index;
            while text
                .get(*index)
                .is_some_and(|byte| matches!(byte, b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9'))
            {
                *index += 1;
            }
            let token = std::str::from_utf8(&text[start..*index]).unwrap_or_default();
            let is_float = token.contains(['.', 'e', 'E']);
            if is_float && token.parse::<f64>().is_ok_and(f64::is_infinite) {
                Shape::Infinite
            } else {
                Shape::Other
            }
        }
        _ => {
            // `true`, `false` or `null`.
            while text.get(*index).is_some_and(u8::is_ascii_alphabetic) {
                *index += 1;
            }
            Shape::Other
        }
    }
}

/// Python's walk: a stack popped from the end, children pushed in order; depth is checked
/// before finiteness on each item.
fn walk_shape(root: &Shape) -> DomainResult<()> {
    let mut pending = vec![(root, 0usize)];
    while let Some((item, depth)) = pending.pop() {
        if depth > 32 {
            return Err(depth_error());
        }
        match item {
            Shape::Infinite => {
                return Err(profile_error("profile_number_invalid", "Use finite JSON numbers.", "$"));
            }
            Shape::Container(children) => pending.extend(children.iter().map(|child| (child, depth + 1))),
            Shape::Other => {}
        }
    }
    Ok(())
}

/// Without an infinite number the walk can only find depth, wherever it looks first.
fn deeper_than_32(value: &Json) -> bool {
    let mut pending = vec![(value, 0usize)];
    while let Some((item, depth)) = pending.pop() {
        if depth > 32 {
            return true;
        }
        match item {
            Json::Object(map) => pending.extend(map.values().map(|child| (child, depth + 1))),
            Json::Array(items) => pending.extend(items.iter().map(|child| (child, depth + 1))),
            _ => {}
        }
    }
    false
}

/// `_parse(source)` for `character_card_import`: the same bounded, unique-member JSON read.
pub(crate) fn parse_profile_json(data: &[u8]) -> DomainResult<Json> {
    parse(data)
}

/// `_parse(source)`: bound both decoding and recursive JSON before structural validation.
fn parse(data: &[u8]) -> DomainResult<Json> {
    if data.is_empty() || data.len() > MAX_PROFILE_BYTES {
        return Err(profile_error(
            "profile_bytes_invalid",
            "Supply a nonempty profile of at most 256 KiB; do not truncate.",
            "$",
        ));
    }
    let malformed = || {
        profile_error(
            "profile_json_invalid",
            "Use unique-member UTF-8 JSON, finite numbers and at most 32 nesting levels.",
            "$",
        )
    };
    let Ok(text) = std::str::from_utf8(data) else { return Err(malformed()) };
    let hooked = match loads_hooked(text, HookPolicy { refuse_duplicates: true, refuse_constants: true }) {
        Ok(hooked) => hooked,
        Err(HookFailure::Decode(error)) => {
            return Err(profile_error(
                "profile_json_invalid",
                &format!("Correct malformed JSON at line {}, column {}.", error.lineno, error.colno),
                "$",
            ))
        }
        Err(HookFailure::Constant(_) | HookFailure::Duplicate(_)) => return Err(malformed()),
        // Row 4: Python reads the nesting and its walk refuses the depth.
        Err(HookFailure::Unsupported(error)) if error.message == "Invalid JSON: recursion limit exceeded" => {
            return Err(depth_error())
        }
        Err(HookFailure::Unsupported(_)) => return Err(malformed()),
    };
    if hooked.non_finite {
        walk_shape(&shape(text.as_bytes(), &mut 0))?;
    } else if deeper_than_32(&hooked.value) {
        return Err(depth_error());
    }
    Ok(hooked.value)
}

/// `type(value) is int and value == expected`.
fn is_int(value: Option<&Json>, expected: i64) -> bool {
    matches!(value, Some(Json::Number(number)) if number.as_i64() == Some(expected))
}

fn items(value: Option<&Json>) -> &[Json] {
    value.and_then(Json::as_array).map(Vec::as_slice).unwrap_or_default()
}

fn text_of<'a>(value: &'a Json, key: &str) -> &'a str {
    value.get(key).and_then(Json::as_str).unwrap_or_default()
}

/// The v2 checks the published schema cannot express: unique IDs per section and safe asset
/// references. The schema has already required each item's string `id` and `uri`.
fn validate_v2(value: &Json) -> DomainResult<()> {
    let sections = [
        ("relationships", items(value.get("relationships"))),
        ("dialogue_examples", items(value.get("dialogue_examples"))),
        ("assets", items(value.get("assets"))),
        ("lore.entries", items(value.get("lore").and_then(|lore| lore.get("entries")))),
    ];
    for (name, section) in sections {
        let mut seen: Vec<&Json> = Vec::new();
        for (index, item) in section.iter().enumerate() {
            let id = &item["id"];
            if seen.contains(&id) {
                return Err(profile_error(
                    "profile_id_duplicate",
                    "Use a unique ID within this section.",
                    &format!("$.{name}[{index}].id"),
                ));
            }
            seen.push(id);
        }
    }
    for (index, asset) in items(value.get("assets")).iter().enumerate() {
        let uri = text_of(asset, "uri");
        let path = format!("$.assets[{index}].uri");
        let invalid = |message: &str| Err(profile_error("profile_asset_invalid", message, &path));
        if uri.chars().any(pystr::is_space) {
            return invalid("Remove whitespace from the asset reference.");
        }
        if uri.starts_with("http://") || uri.starts_with("https://") {
            let Ok(parsed) = urlsplit(uri) else { return invalid("Use a valid HTTP(S) asset reference.") };
            if parsed.hostname().is_none()
                || parsed.username().is_some_and(|name| !name.is_empty())
                || parsed.password().is_some_and(|password| !password.is_empty())
            {
                return invalid("Supply a hostname and remove credentials from the asset reference.");
            }
        } else if let Some(digest) = uri.strip_prefix("asset:sha256:") {
            if !is_sha256(digest) {
                return invalid("Use asset:sha256: followed by exactly 64 lowercase hexadecimal digits.");
            }
        } else if let Some(card) = uri.strip_prefix("card://") {
            if card.is_empty()
                || card.contains('\\')
                || card.starts_with('/')
                || card.split('/').any(|part| part == "..")
            {
                return invalid("Use a bounded relative card asset identity.");
            }
        }
    }
    Ok(())
}

/// `validate_character_profile(source)`: the same published definition for checks, saves and
/// retained-source reads. Python's `str` sources reach it here as their UTF-8 bytes.
pub fn validate_character_profile(source: &[u8]) -> DomainResult<Json> {
    let value = parse(source)?;
    let v2 = value.is_object() && is_int(value.get("format_version"), 2);
    let definition = if v2 { "character_profile_v2" } else { "character_profile" };
    validate_content_shape(definition, &value, Some("character-profile.json"))?;
    if v2 {
        validate_v2(&value)?;
    }
    if canonical_json_bytes(&value).len() > MAX_PROFILE_BYTES {
        return Err(profile_error(
            "profile_bytes_invalid",
            "Canonical profile must fit within 256 KiB; do not truncate.",
            "$",
        ));
    }
    Ok(value)
}

/// `CharacterProfile`: one exact unbound source and its safe catalog projection.
#[derive(Debug, Clone, PartialEq)]
pub struct CharacterProfile {
    pub artifact_id: String,
    pub content_sha256: String,
    pub profile: Json,
}

impl CharacterProfile {
    /// `provenance()`.
    pub fn provenance(&self) -> Json {
        serde_json::json!({
            "artifact_id": self.artifact_id,
            "content_sha256": self.content_sha256,
            "name": self.profile["name"],
        })
    }

    /// `snapshot()`.
    pub fn snapshot(&self) -> Json {
        serde_json::json!({
            "artifact_id": self.artifact_id,
            "content_sha256": self.content_sha256,
            "profile": self.profile,
        })
    }

    /// `catalog_payload()`.
    pub fn catalog_payload(&self) -> Json {
        serde_json::json!({
            "kind": "character_profile",
            "id": self.artifact_id,
            "name": self.profile["name"],
            "title": self.profile["name"],
            "summary": self.profile["summary"],
            "content_sha256": self.content_sha256,
            "reusable": true,
            "campaign_ready": false,
            "adaptation_required": true,
        })
    }
}

/// `set(value) == expected` for a mapping.
fn has_exactly(map: &JsonMap, expected: &[&str]) -> bool {
    map.len() == expected.len() && expected.iter().all(|key| map.contains_key(*key))
}

/// `profile_from_snapshot(value)`: verify retained bytes without depending on a still-present
/// library source.
pub fn profile_from_snapshot(value: &Json) -> DomainResult<CharacterProfile> {
    let Some(map) =
        value.as_object().filter(|map| has_exactly(map, &["artifact_id", "content_sha256", "profile"]))
    else {
        return Err(DomainError::value("Character profile snapshot is invalid"));
    };
    let (Some(artifact_id), Some(digest)) = (map["artifact_id"].as_str(), map["content_sha256"].as_str())
    else {
        return Err(DomainError::value("Character profile identity is invalid"));
    };
    if !is_profile_id(artifact_id) || !is_sha256(digest) {
        return Err(DomainError::value("Character profile identity is invalid"));
    }
    let profile = validate_character_profile(&canonical_json_bytes(&map["profile"]))?;
    if sha256_hex(canonical_json_bytes(&profile)) != digest {
        return Err(DomainError::value("Character profile digest is invalid"));
    }
    if !artifact_id.ends_with(&format!("-{}", &digest[..16])) {
        return Err(DomainError::value("Character profile identity conflicts with its digest"));
    }
    Ok(CharacterProfile { artifact_id: artifact_id.into(), content_sha256: digest.into(), profile })
}

/// `validate_source_profile(value)`: check stored provenance without requiring an upstream
/// artifact to exist.
pub fn validate_source_profile(value: &Json) -> DomainResult<Option<JsonMap>> {
    if value.is_null() {
        return Ok(None);
    }
    let invalid = || DomainError::value("Character source metadata is invalid");
    let Some(map) =
        value.as_object().filter(|map| has_exactly(map, &["artifact_id", "content_sha256", "name"]))
    else {
        return Err(invalid());
    };
    let (Some(artifact_id), Some(digest), Some(name)) =
        (map["artifact_id"].as_str(), map["content_sha256"].as_str(), map["name"].as_str())
    else {
        return Err(invalid());
    };
    if !is_profile_id(artifact_id)
        || !is_sha256(digest)
        || !artifact_id.ends_with(&format!("-{}", &digest[..16]))
        || name.is_empty()
        || name != pystr::strip(name)
        || pystr::len(name) > 160
        || name.contains('\0')
    {
        return Err(invalid());
    }
    Ok(Some(map.clone()))
}

/// `_read(path)`: at most one byte past the limit.
fn read(path: &Path) -> DomainResult<Vec<u8>> {
    let mut data = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(MAX_PROFILE_BYTES as u64 + 1).read_to_end(&mut data))
        .map_err(|error| os_error(&error, path))?;
    Ok(data)
}

/// `_root(storage_root)`: reject linked roots before traversing or writing owner-local content.
fn library_root(storage_root: &Path) -> DomainResult<PathBuf> {
    let root = storage_root.join(LIBRARY);
    if lexists(&root) && (is_link_or_reparse(&root)? || !root.is_dir()) {
        return Err(DomainError::value("Character profile library path is unsafe"));
    }
    Ok(root)
}

/// `load_character_profile(storage_root, artifact_id, expected_content_sha256)`: load only an
/// exact, safe library entry and verify all retained bytes.
pub fn load_character_profile(
    storage_root: &Path,
    artifact_id: &str,
    expected_content_sha256: Option<&str>,
) -> DomainResult<CharacterProfile> {
    if !is_profile_id(artifact_id) {
        return Err(DomainError::value("Character profile identity is invalid"));
    }
    library_root(storage_root)?;
    let Some(target) = validated_library_entry(storage_root, LIBRARY, artifact_id)? else {
        return Err(DomainError::command_rejected_with(
            "Refresh the library and select a saved profile",
            "character_profile_unavailable",
            Vec::new(),
        ));
    };
    validate_owned_files(&target, &FILES)?;
    let record = parse(&read(&target.join("record.json"))?)?;
    let valid = record.as_object().is_some_and(|map| {
        has_exactly(map, &["format_id", "format_version", "artifact_id", "content_sha256"])
            && map["format_id"] == RECORD_FORMAT_ID
            && is_int(map.get("format_version"), 1)
            && map["artifact_id"] == artifact_id
    });
    if !valid {
        return Err(DomainError::value("Character profile record is invalid"));
    }
    let profile = validate_character_profile(&read(&target.join("profile.json"))?)?;
    let source = profile_from_snapshot(&serde_json::json!({
        "artifact_id": artifact_id,
        "content_sha256": record["content_sha256"],
        "profile": profile,
    }))?;
    if expected_content_sha256.is_some_and(|expected| source.content_sha256 != expected) {
        return Err(DomainError::command_rejected_with(
            "Refresh the library; the selected profile does not match",
            "stale_character_profile",
            Vec::new(),
        ));
    }
    Ok(source)
}

/// `tempfile.mkdtemp(prefix=..., dir=...)`'s name: eight characters from `_RandomNameSequence`.
fn temporary_name(prefix: &str, attempt: u32) -> String {
    const CHARACTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    let mut seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos() as u64)
        .unwrap_or_default()
        ^ (u64::from(std::process::id()) << 32)
        ^ u64::from(attempt).wrapping_mul(0x9e37_79b9_7f4a_7c15);
    let name: String = (0..8)
        .map(|_| {
            seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
            CHARACTERS[(seed >> 33) as usize % CHARACTERS.len()] as char
        })
        .collect();
    format!("{prefix}{name}")
}

fn write_synced(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let mut handle = std::fs::File::create(path)?;
    handle.write_all(data)?;
    handle.flush()?;
    handle.sync_all()
}

/// Stage the entry in a fresh `TemporaryDirectory(prefix=".character-profile-save-",
/// dir=storage_root)`, rename it into place, and remove the temporary directory either way.
fn publish(
    storage_root: &Path,
    target: &Path,
    artifact_id: &str,
    files: [(&str, &[u8]); 2],
) -> DomainResult<()> {
    let mut attempt = 0;
    let temporary = loop {
        let candidate = storage_root.join(temporary_name(".character-profile-save-", attempt));
        match std::fs::create_dir(&candidate) {
            Ok(()) => break candidate,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && attempt < 100 => attempt += 1,
            Err(error) => return Err(os_error(&error, &candidate)),
        }
    };
    let staged = temporary.join(artifact_id);
    let result = (|| -> DomainResult<()> {
        std::fs::create_dir(&staged).map_err(|error| os_error(&error, &staged))?;
        for (filename, data) in files {
            let path = staged.join(filename);
            write_synced(&path, data).map_err(|error| os_error(&error, &path))?;
        }
        std::fs::rename(&staged, target).map_err(|error| os_error(&error, &staged))
    })();
    let cleanup = std::fs::remove_dir_all(&temporary).map_err(|error| os_error(&error, &temporary));
    result.and(cleanup)
}

/// `save_character_profile(storage_root, operation_id=..., source=...)`: publish a complete
/// immutable entry atomically; exact retries reuse it.
pub fn save_character_profile(
    storage_root: &Path,
    operation_id: &str,
    source: &[u8],
) -> DomainResult<CharacterProfile> {
    if operation_id.is_empty() {
        return Err(DomainError::value("Character profile save identity is required"));
    }
    let profile = validate_character_profile(source)?;
    let encoded = canonical_json_bytes(&profile);
    let digest = sha256_hex(&encoded);
    let operation_hash = sha256_hex(operation_id.as_bytes());
    let artifact_id = format!("character-profile-{}-{}", &operation_hash[..24], &digest[..16]);
    let _guard = LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let root = library_root(storage_root)?;
    let target = root.join(&artifact_id);
    if lexists(&target) {
        return load_character_profile(storage_root, &artifact_id, Some(&digest));
    }
    std::fs::create_dir_all(&root).map_err(|error| os_error(&error, &root))?;
    let entries = std::fs::read_dir(&root).map_err(|error| os_error(&error, &root))?.count();
    if entries >= 256 {
        return Err(DomainError::value("Character profile library has reached its 256-entry limit"));
    }
    let record = canonical_json_bytes(&serde_json::json!({
        "format_id": RECORD_FORMAT_ID,
        "format_version": 1,
        "artifact_id": artifact_id,
        "content_sha256": digest,
    }));
    publish(storage_root, &target, &artifact_id, [("profile.json", &encoded), ("record.json", &record)])?;
    load_character_profile(storage_root, &artifact_id, Some(&digest))
}

/// The classes `character_profile_catalog` reports and skips: `OSError` (and its subclasses),
/// `UnicodeError`, `ValueError` (and its subclasses) and `CommandRejected`.
fn reported_in_catalog(error: &DomainError) -> bool {
    error.is_value_error()
        || matches!(
            error.python_class(),
            "CommandRejected"
                | "OSError"
                | "FileNotFoundError"
                | "FileExistsError"
                | "PermissionError"
                | "IsADirectoryError"
                | "NotADirectoryError"
                | "UnicodeError"
        )
}

/// `character_profile_catalog(storage_root, on_invalid=...)`: keep good entries discoverable
/// while reporting unsafe or malformed sources.
pub fn character_profile_catalog(
    storage_root: &Path,
    on_invalid: &mut dyn FnMut(&str, &DomainError),
) -> DomainResult<Vec<CharacterProfile>> {
    let root = library_root(storage_root)?;
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut names = Vec::new();
    for entry in std::fs::read_dir(&root).map_err(|error| os_error(&error, &root))? {
        let entry = entry.map_err(|error| os_error(&error, &root))?;
        names.push(entry.file_name().to_string_lossy().into_owned());
    }
    names.sort();
    if names.len() > 256 {
        return Err(DomainError::value("Character profile library exceeds its 256-entry limit"));
    }
    let mut result = Vec::new();
    for name in names {
        match load_character_profile(storage_root, &name, None) {
            Ok(profile) => result.push(profile),
            Err(error) if reported_in_catalog(&error) => {
                on_invalid(if is_profile_id(&name) { &name } else { "invalid-entry" }, &error)
            }
            Err(error) => return Err(error),
        }
    }
    Ok(result)
}
