//! `application/character_card_import.py`: bounded Character Card V1/V2/V3 JSON and PNG
//! conversion into an unbound character profile, with no prompt execution or asset fetching.
//!
//! - [`preview_character_card`]: the card read and mapped, with Python's checks in Python's
//!   order and its conversion report.
//! - [`save_character_card_import`]: the original kept once under `character-card-imports`,
//!   then the profile saved.
//! - [`stage_card_preview`] and [`confirm_card_preview`]: the desktop's two steps, a captured
//!   pending preview under `character-card-previews` and its confirmation.

use crate::character_profiles::{
    load_character_profile, parse_profile_json, save_character_profile, validate_character_profile,
};
use crate::reusable_library_deletion::{is_link_or_reparse, validate_owned_files};
use crate::workspace_files::os_error;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pybase64, pystr, pyurl, Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_eq;
use sylvia_domain::{DomainError, DomainResult};

/// `MAX_CARD_BYTES`.
pub const MAX_CARD_BYTES: usize = 16 * 1024 * 1024;
/// `MAX_PENDING_PREVIEWS`.
pub const MAX_PENDING_PREVIEWS: usize = 64;
/// `_PREVIEW_LOCK`: one staging or confirmation at a time in this process.
static PREVIEW_LOCK: Mutex<()> = Mutex::new(());

/// `COMMON`.
const COMMON: [&str; 6] = ["name", "description", "personality", "scenario", "first_mes", "mes_example"];
/// `V2_TEXT`.
const V2_TEXT: [&str; 5] =
    ["creator_notes", "system_prompt", "post_history_instructions", "creator", "character_version"];

/// A field's Python type in `_check`: `float` is any number (not `bool`).
#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Str,
    Float,
    Bool,
    Dict,
    List,
}

/// `BOOK_FIELDS`.
const BOOK_FIELDS: [(&str, &[Kind]); 7] = [
    ("name", &[Kind::Str]),
    ("description", &[Kind::Str]),
    ("scan_depth", &[Kind::Float]),
    ("token_budget", &[Kind::Float]),
    ("recursive_scanning", &[Kind::Bool]),
    ("extensions", &[Kind::Dict]),
    ("entries", &[Kind::List]),
];

/// `ENTRY_FIELDS`.
const ENTRY_FIELDS: [(&str, &[Kind]); 15] = [
    ("keys", &[Kind::List]),
    ("secondary_keys", &[Kind::List, Kind::Str]),
    ("content", &[Kind::Str]),
    ("extensions", &[Kind::Dict]),
    ("enabled", &[Kind::Bool]),
    ("insertion_order", &[Kind::Float]),
    ("case_sensitive", &[Kind::Bool]),
    ("name", &[Kind::Str]),
    ("priority", &[Kind::Float]),
    ("id", &[Kind::Str, Kind::Float]),
    ("comment", &[Kind::Str]),
    ("selective", &[Kind::Bool]),
    ("constant", &[Kind::Bool]),
    ("position", &[Kind::Str]),
    ("use_regex", &[Kind::Bool]),
];

fn value_error(message: impl Into<String>) -> DomainError {
    DomainError::value(message)
}

/// `_check(value, kind, path)`: the value's exact type (`int` or `float` for a number).
fn check(value: &Json, kinds: &[Kind], path: &str) -> DomainResult<()> {
    let valid = kinds.iter().any(|kind| match kind {
        Kind::Str => value.is_string(),
        Kind::Float => value.is_number(),
        Kind::Bool => value.is_boolean(),
        Kind::Dict => value.is_object(),
        Kind::List => value.is_array(),
    });
    if !valid {
        return Err(value_error(format!(
            "Invalid card field type at {path}; consult the supported card definition"
        )));
    }
    Ok(())
}

/// `_strings(value, path)`.
fn strings(value: &Json, path: &str) -> DomainResult<()> {
    check(value, &[Kind::List], path)?;
    for item in value.as_array().expect("checked") {
        check(item, &[Kind::Str], &format!("{path}[]"))?;
    }
    Ok(())
}

fn u32_at(data: &[u8], offset: usize) -> u32 {
    u32::from_be_bytes(data[offset..offset + 4].try_into().expect("four bytes"))
}

/// `zlib.crc32(data)`.
fn crc32(data: &[u8]) -> u32 {
    let mut crc = flate2::Crc::new();
    crc.update(data);
    crc.sum()
}

/// `_png_json(source)`: the card JSON in a complete PNG's `tEXt` metadata (`ccv3` preferred),
/// and whether both keywords were present.
fn png_json(source: &[u8]) -> DomainResult<(Json, bool)> {
    let mut offset = 8usize;
    let mut found: Vec<(&[u8], Vec<u8>)> = Vec::new();
    let mut chunks = 0usize;
    let (mut ended, mut header, mut image) = (false, false, false);
    while offset < source.len() {
        if offset + 12 > source.len() {
            return Err(value_error("Truncated PNG chunk; use a complete character-card PNG"));
        }
        let size = u32_at(source, offset) as usize;
        let kind = &source[offset + 4..offset + 8];
        let end = offset + 12 + size;
        if end > source.len() || size > MAX_CARD_BYTES || chunks >= 4096 {
            return Err(value_error("PNG chunk/size limit exceeded or truncated"));
        }
        let data = &source[offset + 8..offset + 8 + size];
        let crc = u32_at(source, offset + 8 + size);
        if crc32(&source[offset + 4..offset + 8 + size]) != crc {
            return Err(value_error("PNG checksum invalid; re-export the original card"));
        }
        if chunks == 0 {
            if kind != b"IHDR" || size != 13 {
                return Err(value_error("PNG requires an initial 13-byte IHDR"));
            }
            let (width, height) = (u64::from(u32_at(data, 0)), u64::from(u32_at(data, 4)));
            if width == 0 || height == 0 || width > 8192 || height > 8192 || width * height > 16_777_216 {
                return Err(value_error("PNG portrait dimensions exceed supported asset limits"));
            }
            header = true;
        } else if kind == b"IHDR" {
            return Err(value_error("Ambiguous PNG header"));
        }
        if kind == b"IDAT" {
            image = true;
        }
        if kind == b"tEXt" {
            // `data.partition(b"\0")`.
            let (keyword, encoded, separated) = match data.iter().position(|byte| *byte == 0) {
                Some(at) => (&data[..at], &data[at + 1..], true),
                None => (data, &data[data.len()..], false),
            };
            if keyword == b"chara" || keyword == b"ccv3" {
                if !separated || found.iter().any(|(seen, _)| *seen == keyword) {
                    return Err(value_error("Ambiguous duplicate card metadata in PNG"));
                }
                if encoded.len() > 350_000 {
                    return Err(value_error("Embedded card JSON exceeds 256 KiB"));
                }
                let decoded = pybase64::a2b_base64(encoded, true)
                    .map_err(|_| value_error("Card metadata must be strict base64 UTF-8 JSON"))?;
                found.push((keyword, decoded));
            }
        }
        offset = end;
        chunks += 1;
        if kind == b"IEND" {
            if size != 0 || offset != source.len() {
                return Err(value_error("PNG has trailing data or invalid IEND"));
            }
            ended = true;
            break;
        }
    }
    if !ended || !header || !image || found.is_empty() {
        return Err(value_error("PNG needs complete image chunks and chara/ccv3 tEXt card metadata"));
    }
    let v3 = found.iter().any(|(keyword, _)| *keyword == b"ccv3");
    let selected: &[u8] = if v3 { b"ccv3" } else { b"chara" };
    let bytes = &found.iter().find(|(keyword, _)| *keyword == selected).expect("present").1;
    let value = parse_profile_json(bytes)?;
    if v3 && value.get("spec").is_none_or(|spec| spec != "chara_card_v3") {
        return Err(value_error("ccv3 metadata must identify Character Card V3"));
    }
    Ok((value, found.len() > 1))
}

/// `_asset_uri(uri)`: an inert reference for one card asset.
fn asset_uri(uri: &str) -> DomainResult<String> {
    if pystr::len(uri) > 4096 || uri.contains('\0') || uri.chars().any(pystr::is_space) {
        return Err(value_error("Invalid asset reference; maximum URI length is 4096"));
    }
    if uri.starts_with("http://") || uri.starts_with("https://") {
        let parsed = pyurl::urlsplit(uri).map_err(|error| value_error(error.to_string()))?;
        let present = |part: Option<&str>| part.is_some_and(|part| !part.is_empty());
        if parsed.hostname().is_none() || present(parsed.username()) || present(parsed.password()) {
            return Err(value_error("Invalid remote asset reference; credentials are prohibited"));
        }
        return Ok(uri.to_owned());
    }
    if let Some(path) = uri.strip_prefix("embeded://") {
        // `PurePosixPath(path)`: absolute with a leading `/`; its parts drop empty and `.` ones.
        let parent = path.split('/').any(|part| part == "..");
        if path.is_empty() || path.starts_with('/') || parent || path.contains('\\') || path.contains(':') {
            return Err(value_error("Unsafe embedded asset path"));
        }
        return Ok(format!("card://embedded/{path}"));
    }
    if uri.starts_with("ccdefault:") {
        return Ok(format!("card://default/{}", sha256_hex(uri.as_bytes())));
    }
    if uri.starts_with("data:") {
        // Never decode or expose inline asset bytes as a prompt. Original retains them.
        return Ok(format!("card://inline/{}", sha256_hex(uri.as_bytes())));
    }
    Err(value_error(
        "Unsupported or unsafe asset URI; use http(s), embeded, ccdefault or bounded data references",
    ))
}

/// `_foreign(value, version)`.
fn foreign(value: Json, version: &str) -> Json {
    serde_json::json!({"status": "preserved_only", "format": version, "settings": value})
}

/// `CardPreview`: the converted profile, the source digest and the conversion report.
#[derive(Debug, Clone, PartialEq)]
pub struct CardPreview {
    pub profile: JsonMap,
    pub source_sha256: String,
    pub conversion: JsonMap,
}

impl CardPreview {
    /// `report()`.
    pub fn report(&self) -> JsonMap {
        self.conversion.clone()
    }
}

fn object(value: &mut Json) -> &mut JsonMap {
    value.as_object_mut().expect("an object")
}

fn push(report: &mut JsonMap, list: &str, item: impl Into<String>) {
    report[list].as_array_mut().expect("a list").push(Json::String(item.into()));
}

/// `p.setdefault(key, {})` (or `[]`), as an object or list to fill.
fn setdefault<'a>(profile: &'a mut JsonMap, key: &str, empty: Json) -> &'a mut Json {
    profile.entry(key.to_owned()).or_insert(empty)
}

/// `preview_character_card(source)`: the card converted to an unbound profile, validated, with
/// its report. Nothing is executed, fetched or stored.
pub fn preview_character_card(source: &[u8]) -> DomainResult<CardPreview> {
    if source.is_empty() || source.len() > MAX_CARD_BYTES {
        return Err(value_error("Character card must be nonempty and at most 16 MiB; JSON at most 256 KiB"));
    }
    if source.starts_with(b"PK") {
        return Err(value_error(
            "CHARX/ZIP archives are unsupported; export card JSON or a card PNG. No archive was extracted.",
        ));
    }
    let container = if source.starts_with(b"\x89PNG\r\n\x1a\n") { "png" } else { "json" };
    let (value, dual) =
        if container == "png" { png_json(source)? } else { (parse_profile_json(source)?, false) };
    let Json::Object(value) = value else { return Err(value_error("Card root must be an object")) };
    let spec = value.get("spec");
    let (version, data, outer): (u8, JsonMap, JsonMap) = if spec.is_none()
        && !value.contains_key("spec_version")
        && COMMON.iter().all(|key| value.contains_key(*key))
    {
        (1, value.clone(), JsonMap::new())
    } else if spec.is_some_and(|spec| spec == "chara_card_v2" || spec == "chara_card_v3") {
        let version = if spec.is_some_and(|spec| spec == "chara_card_v2") { 2 } else { 3 };
        if value.get("spec_version").is_none_or(|stated| *stated != format!("{version}.0")) {
            return Err(value_error(
                "Unsupported card version; supported external versions are V1, V2 2.0 and V3 3.0",
            ));
        }
        let data = value.get("data").cloned().unwrap_or(Json::Null);
        check(&data, &[Kind::Dict], "data")?;
        let outer = value
            .iter()
            .filter(|(key, _)| !matches!(key.as_str(), "spec" | "spec_version" | "data"))
            .map(|(key, item)| (key.clone(), item.clone()))
            .collect();
        (version, data.as_object().expect("checked").clone(), outer)
    } else {
        return Err(value_error("Unsupported card envelope; use flat V1 or explicit V2 2.0 / V3 3.0"));
    };
    let required: Vec<&str> =
        COMMON.iter().chain(if version > 1 { V2_TEXT.iter() } else { [].iter() }).copied().collect();
    for key in &required {
        let Some(item) = data.get(*key) else {
            return Err(value_error(format!("Missing required card field: data.{key}")));
        };
        check(item, &[Kind::Str], &format!("data.{key}"))?;
    }
    let text = |key: &str| data[key].as_str().expect("checked").to_owned();
    let name = text("name");
    let stripped = pystr::strip(&name).to_owned();
    if stripped.is_empty() || pystr::len(&stripped) > 160 {
        return Err(value_error("Card name must contain text and fit the native 160-character name limit"));
    }
    if version > 1 {
        for key in ["alternate_greetings", "tags"] {
            let Some(item) = data.get(key) else {
                return Err(value_error(format!("Missing required card array: data.{key}")));
            };
            strings(item, &format!("data.{key}"))?;
        }
        let Some(extensions) = data.get("extensions") else {
            return Err(value_error("Missing required data.extensions object"));
        };
        check(extensions, &[Kind::Dict], "data.extensions")?;
    }
    if version == 3 {
        let Some(item) = data.get("group_only_greetings") else {
            return Err(value_error("Missing required V3 data.group_only_greetings"));
        };
        strings(item, "data.group_only_greetings")?;
    }
    let label = format!("character-card-v{version}");
    let digest = sha256_hex(source);
    let mut report = JsonMap::new();
    for list in ["imported", "transformed", "preserved_only", "unsupported", "omitted"] {
        report.insert(list.into(), Json::Array(Vec::new()));
    }
    let mut p = serde_json::json!({
        "format_id": "sylvia-gm-character-profile",
        "format_version": 2,
        "name": stripped,
        "summary": "Imported character source; explicit target adaptation required.",
        "biography": {"description": text("description"), "personality": text("personality")},
        "portrayal": {"scenario": text("scenario")},
        "greetings": {"default": text("first_mes")},
        "dialogue_examples": [{"id": "card-dialogue", "text": text("mes_example")}],
        "import_source": {"sha256": digest, "format": label, "container": container, "byte_count": source.len()},
    });
    let p_map = object(&mut p);
    for key in COMMON {
        push(&mut report, "imported", key);
    }
    if stripped != name {
        push(&mut report, "transformed", "name: removed outer whitespace; original retained");
    }
    let mut consumed: Vec<&str> = COMMON.to_vec();
    if version > 1 {
        p_map.insert(
            "metadata".into(),
            serde_json::json!({
                "creator": data["creator"],
                "creator_notes": data["creator_notes"],
                "tags": data["tags"],
                "revision": data["character_version"],
            }),
        );
        object(&mut p_map["greetings"]).insert("alternatives".into(), data["alternate_greetings"].clone());
        let names = ["creator", "creator_notes", "tags", "character_version", "alternate_greetings"];
        consumed.extend(names);
        for key in names {
            push(&mut report, "imported", key);
        }
    }
    for key in [
        "nickname",
        "source",
        "creator_notes_multilingual",
        "creation_date",
        "modification_date",
        "group_only_greetings",
    ] {
        let Some(item) = data.get(key) else { continue };
        let path = format!("data.{key}");
        match key {
            "nickname" => {
                check(item, &[Kind::Str], &path)?;
                p_map.insert("identity".into(), serde_json::json!({"aliases": [item]}));
            }
            "group_only_greetings" => {
                strings(item, &path)?;
                object(&mut p_map["greetings"]).insert("group_only".into(), item.clone());
            }
            "source" => {
                strings(item, &path)?;
                object(setdefault(p_map, "metadata", Json::Object(JsonMap::new())))
                    .insert("sources".into(), item.clone());
            }
            "creator_notes_multilingual" => {
                check(item, &[Kind::Dict], &path)?;
                for note in item.as_object().expect("checked").values() {
                    check(note, &[Kind::Str], &path)?;
                }
                let metadata = object(setdefault(p_map, "metadata", Json::Object(JsonMap::new())));
                metadata.insert(key.into(), item.clone());
                let languages: Vec<Json> =
                    item.as_object().expect("checked").keys().map(|key| Json::from(key.as_str())).collect();
                metadata.insert("languages".into(), Json::Array(languages));
            }
            _ => {
                check(item, &[Kind::Float], &path)?;
                object(setdefault(p_map, "metadata", Json::Object(JsonMap::new())))
                    .insert(key.into(), item.clone());
            }
        }
        consumed.push(key);
        push(&mut report, "imported", key);
    }
    if let Some(book) = data.get("character_book") {
        check(book, &[Kind::Dict], "data.character_book")?;
        let book = book.as_object().expect("checked");
        for (key, kinds) in BOOK_FIELDS {
            if let Some(item) = book.get(key) {
                check(item, kinds, &format!("data.character_book.{key}"))?;
            }
        }
        if !book.contains_key("entries") || !book.contains_key("extensions") {
            return Err(value_error("Character book requires entries and extensions"));
        }
        let mut lore = JsonMap::new();
        lore.insert("entries".into(), Json::Array(Vec::new()));
        for key in ["name", "description"] {
            if let Some(item) = book.get(key) {
                lore.insert(key.into(), item.clone());
            }
        }
        for (index, entry) in book["entries"].as_array().expect("checked").iter().enumerate() {
            check(entry, &[Kind::Dict], &format!("character_book.entries[{index}]"))?;
            let fields = entry.as_object().expect("checked");
            for key in ["keys", "content", "extensions", "enabled", "insertion_order"] {
                if !fields.contains_key(key) {
                    return Err(value_error(format!(
                        "Missing lore field: character_book.entries[{index}].{key}"
                    )));
                }
            }
            if version == 3 && !fields.contains_key("use_regex") {
                return Err(value_error(format!(
                    "Missing V3 lore field: character_book.entries[{index}].use_regex"
                )));
            }
            for (key, kinds) in ENTRY_FIELDS {
                if let Some(item) = fields.get(key) {
                    check(item, kinds, &format!("character_book.entries[{index}].{key}"))?;
                }
            }
            strings(&fields["keys"], "character_book.entries[].keys")?;
            if let Some(secondary @ Json::Array(_)) = fields.get("secondary_keys") {
                strings(secondary, "character_book.entries[].secondary_keys")?;
            }
            let mut mapped = JsonMap::new();
            mapped.insert("id".into(), format!("card-lore-{index}").into());
            for (key, _) in ENTRY_FIELDS {
                let Some(item) = fields.get(key) else { continue };
                if key == "id" {
                    mapped.insert("source_id".into(), item.clone());
                } else if key != "extensions" {
                    mapped.insert(key.into(), item.clone());
                }
            }
            let known = |key: &str| ENTRY_FIELDS.iter().any(|(name, _)| *name == key);
            let extra: JsonMap = fields
                .iter()
                .filter(|(key, _)| !known(key) || key.as_str() == "extensions")
                .map(|(key, item)| (key.clone(), item.clone()))
                .collect();
            if !extra.is_empty() {
                mapped.insert("foreign_configuration".into(), foreign(Json::Object(extra), &label));
            }
            lore["entries"].as_array_mut().expect("a list").push(Json::Object(mapped));
        }
        let settings: JsonMap = book
            .iter()
            .filter(|(key, _)| !matches!(key.as_str(), "name" | "description" | "entries"))
            .map(|(key, item)| (key.clone(), item.clone()))
            .collect();
        if !settings.is_empty() {
            lore.insert("foreign_configuration".into(), foreign(Json::Object(settings), &label));
        }
        p_map.insert("lore".into(), Json::Object(lore));
        consumed.push("character_book");
        push(&mut report, "imported", "character_book: text, keys, source IDs, order and enabled flags");
        push(
            &mut report,
            "preserved_only",
            "lore activation, regex, insertion and extension settings: no scanning or prompt injection",
        );
    }
    if let Some(assets) = data.get("assets") {
        check(assets, &[Kind::List], "data.assets")?;
        let mut mapped = Vec::new();
        for (index, asset) in assets.as_array().expect("checked").iter().enumerate() {
            check(asset, &[Kind::Dict], "data.assets[]")?;
            let fields = asset.as_object().expect("checked");
            for key in ["type", "uri", "name", "ext"] {
                let Some(item) = fields.get(key) else {
                    return Err(value_error(format!("Missing asset field: data.assets[].{key}")));
                };
                check(item, &[Kind::Str], &format!("data.assets[].{key}"))?;
            }
            mapped.push(serde_json::json!({
                "id": format!("card-asset-{index}"),
                "kind": fields["type"],
                "name": fields["name"],
                "format": fields["ext"],
                "uri": asset_uri(fields["uri"].as_str().expect("checked"))?,
                "status": "reference_only",
            }));
        }
        p_map.insert("assets".into(), Json::Array(mapped));
        push(
            &mut report,
            "transformed",
            "assets: inert references; embedded/data URIs identify retained original content",
        );
        // Retain full descriptors (including unknown asset metadata) as preserved-only.
    }
    if container == "png" {
        setdefault(p_map, "assets", Json::Array(Vec::new())).as_array_mut().expect("a list").push(
            serde_json::json!({
                "id": "card-portrait",
                "uri": "card://portrait",
                "kind": "icon",
                "status": "reference_only",
            }),
        );
        push(
            &mut report,
            "preserved_only",
            "PNG portrait and auxiliary chunks retained in original; no image decode or asset loading",
        );
    }
    let mut foreign_fields: JsonMap = data
        .iter()
        .filter(|(key, _)| !consumed.contains(&key.as_str()))
        .map(|(key, item)| (key.clone(), item.clone()))
        .collect();
    let prefix = if version > 1 && !outer.is_empty() { "data." } else { "" };
    let mut sorted: Vec<&String> = foreign_fields.keys().collect();
    sorted.sort();
    let names: Vec<String> = sorted.into_iter().map(|key| format!("{prefix}{key}")).collect();
    for name in names {
        push(&mut report, "preserved_only", name);
    }
    if !outer.is_empty() {
        // A card may itself contain any of these names. Never overwrite source fields.
        let mut outer_key = "outer_extensions".to_owned();
        if foreign_fields.contains_key(&outer_key) {
            outer_key = "outer_extensions_root".to_owned();
            let mut suffix = 0;
            while foreign_fields.contains_key(&outer_key) {
                suffix += 1;
                outer_key = format!("outer_extensions_root_{suffix}");
            }
            push(
                &mut report,
                "transformed",
                format!(
                    "Root fields grouped under foreign_configuration.settings.{outer_key}; colliding data fields retained unchanged"
                ),
            );
        }
        let mut keys: Vec<&String> = outer.keys().collect();
        keys.sort();
        let names: Vec<String> = keys
            .into_iter()
            .map(|key| format!("root.{key}: foreign_configuration.settings.{outer_key}"))
            .collect();
        foreign_fields.insert(outer_key, Json::Object(outer));
        for name in names {
            push(&mut report, "preserved_only", name);
        }
    }
    if !foreign_fields.is_empty() {
        p_map.insert("foreign_configuration".into(), foreign(Json::Object(foreign_fields), &label));
    }
    push(
        &mut report,
        "unsupported",
        "foreign prompt precedence, macros, scripts, automatic lore activation, external asset loading",
    );
    if dual {
        push(
            &mut report,
            "transformed",
            "PNG has chara and ccv3: V3 selected per spec; V2 retained in original",
        );
    }
    report.insert(
        "omitted".into(),
        serde_json::json!(["No source bytes discarded; unsupported behavior is not activated."]),
    );
    validate_character_profile(&canonical_json_bytes(&p))?;
    let Json::Object(profile) = p else { unreachable!("built as an object") };
    Ok(CardPreview { profile, source_sha256: digest, conversion: report })
}

/// `os.path.lexists(path)`.
fn lexists(path: &Path) -> bool {
    std::fs::symlink_metadata(path).is_ok()
}

fn is_hex64(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

/// `_original_path(storage_root, digest)`: one retained original's folder, refused when linked
/// or holding anything but its two files.
fn original_path(storage_root: &Path, digest: &str) -> DomainResult<PathBuf> {
    if !is_hex64(digest) {
        return Err(value_error("Invalid import source identity"));
    }
    let root = storage_root.join("character-card-imports");
    if lexists(&root) && (is_link_or_reparse(&root)? || !root.is_dir()) {
        return Err(value_error("Unsafe imported-source library"));
    }
    let target = root.join(digest);
    if lexists(&target) {
        if is_link_or_reparse(&target)? || !target.is_dir() {
            return Err(value_error("Unsafe imported-source entry"));
        }
        validate_owned_files(&target, &["source.bin", "record.json"])?;
    }
    Ok(target)
}

/// The retained original's record.
fn source_record(digest: &str, byte_count: usize) -> Json {
    serde_json::json!({
        "format_id": "sylvia-gm-character-card-source",
        "format_version": 1,
        "sha256": digest,
        "byte_count": byte_count,
    })
}

/// `handle.read(MAX_CARD_BYTES + 1)` of an opened file.
fn read_capped(path: &Path) -> DomainResult<Vec<u8>> {
    let mut data = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(MAX_CARD_BYTES as u64 + 1).read_to_end(&mut data))
        .map_err(|error| os_error(&error, path))?;
    Ok(data)
}

/// `load_import_original(storage_root, digest)`: the retained original, checked against its
/// record and digest.
pub fn load_import_original(storage_root: &Path, digest: &str) -> DomainResult<Vec<u8>> {
    let target = original_path(storage_root, digest)?;
    let record_path = target.join("record.json");
    let record =
        parse_profile_json(&std::fs::read(&record_path).map_err(|error| os_error(&error, &record_path))?)?;
    let source = read_capped(&target.join("source.bin"))?;
    if !py_eq(&record, &source_record(digest, source.len()))
        || source.len() > MAX_CARD_BYTES
        || sha256_hex(&source) != digest
    {
        return Err(value_error("Imported original identity/checksum is invalid"));
    }
    Ok(source)
}

/// A new file written and fsynced (`open("wb")` or `open("xb")`).
fn write_synced(path: &Path, data: &[u8], exclusive: bool) -> DomainResult<()> {
    let mut options = std::fs::OpenOptions::new();
    options.write(true);
    if exclusive {
        options.create_new(true);
    } else {
        options.create(true).truncate(true);
    }
    let written = options.open(path).and_then(|mut file| {
        file.write_all(data)?;
        file.flush()?;
        file.sync_all()
    });
    written.map_err(|error| os_error(&error, path))
}

/// `{**saved.catalog_payload(), "import_source_sha256": ..., "conversion_preview": ...}`.
fn imported_payload(
    saved: &crate::character_profiles::CharacterProfile,
    digest: &str,
    preview: &CardPreview,
) -> JsonMap {
    let mut payload = match saved.catalog_payload() {
        Json::Object(map) => map,
        _ => JsonMap::new(),
    };
    payload.insert("import_source_sha256".into(), digest.into());
    payload.insert("conversion_preview".into(), Json::Object(preview.report()));
    payload
}

/// `save_character_card_import(storage_root, operation_id=..., source=...)`: the original kept
/// once, then the converted profile saved.
pub fn save_character_card_import(
    storage_root: &Path,
    operation_id: &str,
    source: &[u8],
) -> DomainResult<JsonMap> {
    let preview = preview_character_card(source)?;
    let target = original_path(storage_root, &preview.source_sha256)?;
    if target.exists() {
        if load_import_original(storage_root, &preview.source_sha256)? != source {
            return Err(value_error("Original-source conflict"));
        }
    } else {
        let parent = target.parent().expect("a library folder");
        std::fs::create_dir_all(parent).map_err(|error| os_error(&error, parent))?;
        let temporary = crate::plan_bundle::TemporaryDirectory::new(storage_root, ".card-import-")?;
        let staged = temporary.path.join(&preview.source_sha256);
        std::fs::create_dir(&staged).map_err(|error| os_error(&error, &staged))?;
        let record = canonical_json_bytes(&source_record(&preview.source_sha256, source.len()));
        for (name, data) in [("source.bin", source), ("record.json", record.as_slice())] {
            write_synced(&staged.join(name), data, false)?;
        }
        match std::fs::rename(&staged, &target) {
            Ok(()) => {}
            // `except FileExistsError`: another save of the same original won the race.
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                if load_import_original(storage_root, &preview.source_sha256)? != source {
                    return Err(value_error("Original-source conflict"));
                }
            }
            Err(error) => return Err(os_error(&error, &staged)),
        }
    }
    let saved = save_character_profile(
        storage_root,
        operation_id,
        &canonical_json_bytes(&Json::Object(preview.profile.clone())),
    )?;
    Ok(imported_payload(&saved, &preview.source_sha256, &preview))
}

/// `_preview_root(storage_root)`.
fn preview_root(storage_root: &Path) -> DomainResult<PathBuf> {
    let root = storage_root.join("character-card-previews");
    if lexists(&root) && (is_link_or_reparse(&root)? || !root.is_dir()) {
        return Err(value_error("Unsafe preview storage"));
    }
    Ok(root)
}

/// `_read_pending_preview(target, digest)`.
fn read_pending_preview(target: &Path, digest: &str) -> DomainResult<Vec<u8>> {
    if is_link_or_reparse(target)? || !target.is_file() {
        return Err(value_error("Unsafe preview entry"));
    }
    let source = read_capped(target)?;
    if source.len() > MAX_CARD_BYTES || sha256_hex(&source) != digest {
        return Err(value_error("Pending preview changed; preview the source again"));
    }
    Ok(source)
}

/// `_confirmed_card_result(storage_root, digest, source)`: a prior desktop confirmation
/// verified without creating a profile on a retry.
fn confirmed_card_result(storage_root: &Path, digest: &str, source: Option<&[u8]>) -> DomainResult<JsonMap> {
    let original = load_import_original(storage_root, digest)?;
    if source.is_some_and(|source| source != original) {
        return Err(value_error("Original-source conflict"));
    }
    let preview = preview_character_card(&original)?;
    let profile_digest = sha256_hex(canonical_json_bytes(&Json::Object(preview.profile.clone())));
    let operation_hash = &sha256_hex(format!("desktop-card:{digest}").as_bytes())[..24];
    let profile_id = format!("character-profile-{operation_hash}-{}", &profile_digest[..16]);
    let saved = load_character_profile(storage_root, &profile_id, Some(&profile_digest))?;
    Ok(imported_payload(&saved, digest, &preview))
}

/// `_retire_pending_preview(storage_root, digest, source)`: only a verified temporary duplicate
/// is removed, after durable confirmation.
fn retire_pending_preview(storage_root: &Path, digest: &str, source: &[u8]) -> DomainResult<()> {
    confirmed_card_result(storage_root, digest, Some(source))?;
    let target = preview_root(storage_root)?.join(digest);
    if read_pending_preview(&target, digest)? != source {
        return Err(value_error("Pending preview changed; preview the source again"));
    }
    std::fs::remove_file(&target).map_err(|error| os_error(&error, &target))
}

/// `except (OSError, ValueError, CommandRejected)`, by Python's class.
fn retirement_skipped(error: &DomainError) -> bool {
    error.is_value_error()
        || matches!(
            error.python_class(),
            "OSError"
                | "FileNotFoundError"
                | "FileExistsError"
                | "PermissionError"
                | "IsADirectoryError"
                | "NotADirectoryError"
                | "ValueError"
                | "JSONDecodeError"
                | "UnicodeError"
                | "UnicodeDecodeError"
                | "UnicodeEncodeError"
                | "ContentShapeError"
                | "CommandRejected"
                | "MechanicsRejected"
                | "RulesetPreconditionRejected"
                | "PortableRecoveryFailure"
                | "_BackupFailure"
                | "_ExportFailure"
        )
}

/// `_retire_completed_previews(storage_root, root)`: old completed staging slots released;
/// pending, unknown and unsafe entries kept.
fn retire_completed_previews(storage_root: &Path, root: &Path) -> DomainResult<()> {
    for entry in std::fs::read_dir(root).map_err(|error| os_error(&error, root))? {
        let target = entry.map_err(|error| os_error(&error, root))?.path();
        let name = target.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
        if !is_hex64(&name) {
            continue;
        }
        let retired = (|| -> DomainResult<()> {
            if !original_path(storage_root, &name)?.exists() {
                return Ok(());
            }
            let source = read_pending_preview(&target, &name)?;
            retire_pending_preview(storage_root, &name, &source)
        })();
        match retired {
            Err(error) if !retirement_skipped(&error) => return Err(error),
            // Failed saves, tampered content and paths we do not own still consume a slot.
            _ => {}
        }
    }
    Ok(())
}

/// `stage_card_preview(storage_root, source_path)`: one desktop-selected local file captured
/// as a pending preview, never trusted as executable content.
pub fn stage_card_preview(storage_root: &Path, source_path: &Path) -> DomainResult<JsonMap> {
    let suffix = source_path.extension().map(|suffix| suffix.to_string_lossy().to_lowercase());
    let allowed = matches!(suffix.as_deref(), Some("json" | "png"));
    if !allowed || is_link_or_reparse(source_path)? || !source_path.is_file() {
        return Err(value_error("Select a regular local .json or .png character-card file"));
    }
    let source = read_capped(source_path)?;
    let preview = preview_character_card(&source)?;
    {
        let _lock = PREVIEW_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let root = preview_root(storage_root)?;
        std::fs::create_dir_all(&root).map_err(|error| os_error(&error, &root))?;
        retire_completed_previews(storage_root, &root)?;
        let target = root.join(&preview.source_sha256);
        if lexists(&target) {
            if read_pending_preview(&target, &preview.source_sha256)? != source {
                return Err(value_error("Preview identity conflict"));
            }
        } else {
            let count = std::fs::read_dir(&root).map_err(|error| os_error(&error, &root))?.count();
            if count >= MAX_PENDING_PREVIEWS {
                return Err(value_error(
                    "Preview capacity reached; confirm an existing pending preview before adding another",
                ));
            }
            write_synced(&target, &source, true)?;
        }
    }
    let description = preview.profile["biography"]["description"].as_str().unwrap_or_default();
    let mut reply = JsonMap::new();
    reply.insert("status".into(), "preview".into());
    reply.insert("source_sha256".into(), preview.source_sha256.clone().into());
    reply.insert("name".into(), preview.profile["name"].clone());
    reply.insert("summary".into(), description.chars().take(320).collect::<String>().into());
    reply.insert("conversion_preview".into(), Json::Object(preview.report()));
    reply.insert("campaign_ready".into(), false.into());
    reply.insert("adaptation_required".into(), true.into());
    Ok(reply)
}

/// `confirm_card_preview(storage_root, digest)`: the exact pending preview imported and its
/// slot retired, or a prior confirmation answered again.
pub fn confirm_card_preview(storage_root: &Path, digest: &str) -> DomainResult<JsonMap> {
    if !is_hex64(digest) {
        return Err(value_error("Select the exact pending card preview"));
    }
    let result = {
        let _lock = PREVIEW_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let target = preview_root(storage_root)?.join(digest);
        if lexists(&target) {
            let source = read_pending_preview(&target, digest)?;
            let result =
                save_character_card_import(storage_root, &format!("desktop-card:{digest}"), &source)?;
            retire_pending_preview(storage_root, digest, &source)?;
            result
        } else {
            match confirmed_card_result(storage_root, digest, None) {
                Ok(result) => result,
                Err(error)
                    if matches!(
                        error.python_class(),
                        "FileNotFoundError"
                            | "CommandRejected"
                            | "MechanicsRejected"
                            | "RulesetPreconditionRejected"
                            | "PortableRecoveryFailure"
                            | "_BackupFailure"
                            | "_ExportFailure"
                    ) =>
                {
                    return Err(value_error(
                        "Pending preview unavailable; select and preview the source again",
                    ));
                }
                Err(error) => return Err(error),
            }
        }
    };
    let mut reply = JsonMap::new();
    reply.insert("status".into(), "imported".into());
    reply.insert("profile".into(), Json::Object(result));
    Ok(reply)
}
