//! `application/desktop_update/manifest.py`: the bounded, signed identity of one desktop
//! release, independent of downloading or running it.
//!
//! The checks run in Python's order, because the first failing check names the refusal.
//! `update_vectors` compares every refusal and every accepted manifest with the snapshot.

use super::update_error;
use super::url::{has_bare_percent, unquote_strict, urlsplit};
use serde::de::{Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use std::cmp::Ordering;
use std::collections::{BTreeMap, HashSet};
use std::sync::OnceLock;
use sylvia_base::pyjson;
use sylvia_base::{CodedError, CodedResult, Json};

/// `MANIFEST_LIMIT`.
pub const MANIFEST_LIMIT: usize = 2 * 1024 * 1024;
/// `ARTIFACT_LIMIT`.
pub const ARTIFACT_LIMIT: u64 = 512 * 1024 * 1024;
/// `EXPANDED_LIMIT`.
pub const EXPANDED_LIMIT: u64 = 2 * 1024 * 1024 * 1024;
/// `OFFICIAL_RELEASES`.
pub const OFFICIAL_RELEASES: &str = "https://api.github.com/repos/pneuma-io/sylvia-gm/releases?per_page=30";
/// `sylvia_gm.__release_channel__`: the channel the running build declares.
pub const RELEASE_CHANNEL: &str = "alpha";
/// `sylvia_gm.__version__`.
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

/// `_MUTABLE`: top-level data folders an application payload may never write.
const MUTABLE: [&str; 10] = [
    "isolated-data",
    "campaigns",
    "participants",
    "builder-chats",
    "migration-sources",
    "webview",
    "diagnostics",
    "temp",
    "dictation-models",
    ".application-updates",
];

/// `portable_artifact.LEGAL_FILES`.
pub const LEGAL_FILES: [&str; 4] =
    ["LICENSE", "COPYRIGHT", "LICENSING-STATUS.md", "SYLVIA-ADDITIONAL-PERMISSIONS.md"];
/// `portable_artifact.ENTRY_MANIFEST`.
pub const ENTRY_MANIFEST: &str = "ENTRY-HASHES.json";

/// `portable_artifact.REQUIRED_PAYLOADS`.
pub fn required_payloads() -> Vec<String> {
    let mut names: Vec<String> = [
        "Sylvia GM.exe",
        "sylvia-gm-owner.exe",
        "sylvia-portable.json",
        "README-PORTABLE.txt",
        "SRD-Lite.sylvia-gm-package",
        "Severed-Lands.sylvia-gm-package",
    ]
    .into_iter()
    .map(str::to_string)
    .collect();
    names.extend(LEGAL_FILES.iter().map(|name| name.to_string()));
    names.extend(LEGAL_FILES.iter().map(|name| format!("licenses/{name}")));
    names.extend(
        ["licenses/SRD-5.2.1.md", "licenses/Severed-Lands.md", "licenses/dependency-notices.txt"]
            .into_iter()
            .map(str::to_string),
    );
    names
}

fn invalid_version() -> CodedError {
    update_error("invalid_version", "The release version is not valid SemVer.")
}

/// `Version`: a SemVer release identity. Equality and order ignore build metadata, as
/// Python's `__eq__` and `__lt__` do; `text` keeps it.
#[derive(Debug, Clone)]
pub struct Version {
    pub text: String,
    pub core: (u32, u32, u32),
    pub prerelease: Vec<String>,
}

fn version_pattern() -> &'static regex::Regex {
    static PATTERN: OnceLock<regex::Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        regex::Regex::new(concat!(
            r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})",
            r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?",
            r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$",
        ))
        .expect("the SemVer pattern compiles")
    })
}

/// `str.isdigit()` for an identifier the SemVer pattern admitted (ASCII only).
fn is_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit())
}

/// `int(left) < int(right)` for digit strings of any length without leading zeros.
fn numeric_order(left: &str, right: &str) -> Ordering {
    let left = left.trim_start_matches('0');
    let right = right.trim_start_matches('0');
    left.len().cmp(&right.len()).then_with(|| left.cmp(right))
}

impl Version {
    /// `Version.parse(value)`.
    pub fn parse(value: &Json) -> CodedResult<Version> {
        match value {
            Json::String(text) => Self::parse_text(text),
            _ => Err(invalid_version()),
        }
    }

    /// `Version.parse(text)` for a string.
    pub fn parse_text(text: &str) -> CodedResult<Version> {
        if text.chars().count() > 128 {
            return Err(invalid_version());
        }
        let captures = version_pattern().captures(text).ok_or_else(invalid_version)?;
        let number =
            |index: usize| -> CodedResult<u32> { captures[index].parse().map_err(|_| invalid_version()) };
        let core = (number(1)?, number(2)?, number(3)?);
        let prerelease: Vec<String> = captures
            .get(4)
            .map(|found| found.as_str().split('.').map(str::to_string).collect())
            .unwrap_or_default();
        if prerelease.iter().any(|part| is_digits(part) && part.len() > 1 && part.starts_with('0')) {
            return Err(invalid_version());
        }
        Ok(Version { text: text.to_string(), core, prerelease })
    }

    /// `Version.channel`.
    pub fn channel(&self) -> &str {
        self.prerelease.first().map_or("stable", String::as_str)
    }

    /// `self < other` (`Version.__lt__`).
    fn less(&self, other: &Version) -> bool {
        if self.core != other.core {
            return self.core < other.core;
        }
        if self.prerelease.is_empty() || other.prerelease.is_empty() {
            return !self.prerelease.is_empty() && other.prerelease.is_empty();
        }
        for (left, right) in self.prerelease.iter().zip(&other.prerelease) {
            if left == right {
                continue;
            }
            if is_digits(left) && is_digits(right) {
                return numeric_order(left, right) == Ordering::Less;
            }
            if is_digits(left) != is_digits(right) {
                return is_digits(left);
            }
            return left < right;
        }
        self.prerelease.len() < other.prerelease.len()
    }
}

impl PartialEq for Version {
    fn eq(&self, other: &Self) -> bool {
        (self.core, &self.prerelease) == (other.core, &other.prerelease)
    }
}

impl Eq for Version {}

impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Version {
    /// `functools.total_ordering` over `__lt__` and `__eq__`.
    fn cmp(&self, other: &Self) -> Ordering {
        if self.less(other) {
            Ordering::Less
        } else if self == other {
            Ordering::Equal
        } else {
            Ordering::Greater
        }
    }
}

/// `binascii.a2b_base64(data, strict_mode=True)`, as `base64.b64decode(..., validate=True)`
/// calls it. Python does not check that the unused low bits of the last character are zero,
/// so neither does this. `None` where Python raises.
pub fn b64decode_strict(text: &str) -> Option<Vec<u8>> {
    if !text.is_ascii() {
        return None;
    }
    let data = text.as_bytes();
    if data.first() == Some(&b'=') {
        return None;
    }
    let value = |byte: u8| -> Option<u8> {
        match byte {
            b'A'..=b'Z' => Some(byte - b'A'),
            b'a'..=b'z' => Some(byte - b'a' + 26),
            b'0'..=b'9' => Some(byte - b'0' + 52),
            b'+' => Some(62),
            b'/' => Some(63),
            _ => None,
        }
    };
    let mut output = Vec::with_capacity(data.len() / 4 * 3);
    let mut quad = 0u8;
    let mut left = 0u8;
    let mut pads = 0u8;
    let mut padding = false;
    for (index, byte) in data.iter().enumerate() {
        if *byte == b'=' {
            padding = true;
            if quad == 0 {
                return None;
            }
            pads += 1;
            if quad >= 2 && quad + pads >= 4 {
                if index + 1 < data.len() {
                    return None;
                }
                return Some(output);
            }
            continue;
        }
        let bits = value(*byte)?;
        if padding {
            return None;
        }
        pads = 0;
        match quad {
            0 => {
                quad = 1;
                left = bits;
            }
            1 => {
                quad = 2;
                output.push((left << 2) | (bits >> 4));
                left = bits & 0x0f;
            }
            2 => {
                quad = 3;
                output.push((left << 4) | (bits >> 2));
                left = bits & 0x03;
            }
            _ => {
                quad = 0;
                output.push((left << 6) | bits);
                left = 0;
            }
        }
    }
    (quad == 0).then_some(output)
}

/// `KeyRing`: only public, build-pinned keys. There is no signing or key generation here.
#[derive(Debug, Clone, Default)]
pub struct KeyRing {
    keys: BTreeMap<String, [u8; 32]>,
}

impl KeyRing {
    /// `KeyRing(keys)`.
    pub fn new(keys: BTreeMap<String, [u8; 32]>) -> Self {
        Self { keys }
    }

    /// Whether any key is pinned (`bool(keyring.keys)`).
    pub fn has_keys(&self) -> bool {
        !self.keys.is_empty()
    }

    /// `KeyRing.verify(key_id, message, signature)`.
    pub fn verify(&self, key_id: &str, message: &[u8], signature: &[u8]) -> CodedResult<()> {
        if self.keys.is_empty() {
            return Err(update_error(
                "trust_not_configured",
                "Updates are not configured in this build. A manual bootstrap upgrade is required.",
            ));
        }
        let untrusted = || update_error("invalid_signature", "The update signature is not trusted.");
        let key = self.keys.get(key_id).ok_or_else(untrusted)?;
        let signature: [u8; 64] = signature.try_into().map_err(|_| untrusted())?;
        if ed25519_verifies(key, message, &signature) {
            Ok(())
        } else {
            Err(untrusted())
        }
    }
}

/// OpenSSL 3.0's Ed25519 verification, which `cryptography`'s `Ed25519PublicKey.verify` calls:
/// it refuses `S >= L` and an `R` that is not the canonical encoding of the recomputed point,
/// but accepts a small-order public key, also in a non-canonical encoding. That is
/// `ed25519-dalek`'s `verify`, not `verify_strict` (which refuses weak keys);
/// `update_vectors::signatures_verify_like_openssl` holds the cases that decide it.
fn ed25519_verifies(key: &[u8; 32], message: &[u8], signature: &[u8; 64]) -> bool {
    use ed25519_dalek::Verifier;
    let Ok(key) = ed25519_dalek::VerifyingKey::from_bytes(key) else {
        return false;
    };
    let signature = ed25519_dalek::Signature::from_bytes(signature);
    key.verify(message, &signature).is_ok()
}

fn untrusted_origin() -> CodedError {
    update_error("untrusted_origin", "The update does not use an official release location.")
}

/// `release_url(value, version, distribution)`: an exact official GitHub release download.
pub fn release_url(value: &Json, version: &str, distribution: Option<&str>) -> CodedResult<String> {
    let Json::String(value) = value else {
        return Err(untrusted_origin());
    };
    if value.chars().count() > 2048 || value.chars().any(|c| (c as u32) <= 32) || value.contains('\\') {
        return Err(untrusted_origin());
    }
    let invalid = || update_error("untrusted_origin", "The update location is invalid.");
    let parsed = urlsplit(value).map_err(|_| invalid())?;
    let path = unquote_strict(&parsed.path).map_err(|_| invalid())?;
    let expected = format!("/pneuma-io/sylvia-gm/releases/download/v{version}/");
    let tail = path.strip_prefix(&expected);
    if parsed.scheme != "https"
        || parsed.netloc != "github.com"
        || !parsed.query.is_empty()
        || !parsed.fragment.is_empty()
        || has_bare_percent(&parsed.path)
        || tail.is_none()
        || path.contains('\\')
        || path.chars().any(|c| (c as u32) < 32)
        || tail.is_some_and(|tail| tail.contains('/') || matches!(tail, "" | "." | ".."))
    {
        return Err(untrusted_origin());
    }
    let suffix = match distribution {
        Some("installed") => Some("-setup.exe"),
        Some("portable") => Some("-portable.zip"),
        _ => None,
    };
    if let Some(suffix) = suffix {
        if !path.to_lowercase().ends_with(suffix) {
            return Err(update_error(
                "wrong_distribution",
                "The update artifact does not match this distribution.",
            ));
        }
    }
    Ok(value.clone())
}

/// The Windows device names `payload_path` refuses.
fn reserved_device(name: &str) -> bool {
    matches!(name, "CON" | "PRN" | "AUX" | "NUL")
        || ["COM", "LPT"].iter().any(|prefix| {
            name.strip_prefix(prefix)
                .is_some_and(|digit| matches!(digit, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"))
        })
}

/// `payload_path(value)`: a relative ASCII application path outside every data folder.
/// Python's `str(PurePosixPath(value)) != value` test is implied by the part checks.
pub fn payload_path(value: &Json) -> CodedResult<String> {
    let unsafe_path = || update_error("unsafe_payload", "The update contains an unsafe application path.");
    let Json::String(value) = value else {
        return Err(unsafe_path());
    };
    if value.is_empty() || value.len() > 240 || !value.is_ascii() {
        return Err(unsafe_path());
    }
    let parts: Vec<&str> = value.split('/').collect();
    let lowered = value.to_ascii_lowercase();
    if value.starts_with('/')
        || parts.iter().any(|part| matches!(*part, "" | "." | ".."))
        || parts.iter().any(|part| {
            !part
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b' ' | b'-'))
                || part.ends_with(['.', ' '])
        })
        || parts.iter().any(|part| {
            let stem = part.split('.').next().unwrap_or_default().trim_end_matches([' ', '.']);
            reserved_device(&stem.to_ascii_uppercase())
        })
        || MUTABLE.contains(&parts[0].to_ascii_lowercase().as_str())
        || matches!(lowered.as_str(), "authority.sqlite3" | "config.yaml")
    {
        return Err(unsafe_path());
    }
    Ok(value.clone())
}

/// `PayloadFile`: one signed application file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PayloadFile {
    pub path: String,
    pub size: u64,
    pub sha256: String,
}

/// `Manifest`: an authenticated, parsed release.
#[derive(Debug, Clone)]
pub struct Manifest {
    pub version: Version,
    pub channel: String,
    pub target: String,
    pub distribution: String,
    pub notes: String,
    pub artifact_url: String,
    pub artifact_size: u64,
    pub artifact_sha256: String,
    pub files: Vec<PayloadFile>,
    pub signed_bytes: Vec<u8>,
    pub envelope_bytes: Vec<u8>,
}

/// `_size(value, maximum)`: an integer (never a boolean) in `1..=maximum`.
fn size(value: &Json, maximum: u64) -> CodedResult<u64> {
    match value.as_u64() {
        Some(number) if value.is_number() && (1..=maximum).contains(&number) => Ok(number),
        _ => Err(update_error("invalid_metadata", "The update contains an invalid size.")),
    }
}

/// `_hash(value)`: 64 lowercase hex digits.
fn content_hash(value: &Json) -> CodedResult<String> {
    match value {
        Json::String(text)
            if text.len() == 64 && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')) =>
        {
            Ok(text.clone())
        }
        _ => Err(update_error("invalid_metadata", "The update contains an invalid content identity.")),
    }
}

/// `set(object) == expected`.
fn has_exactly(object: &sylvia_base::JsonMap, expected: &[&str]) -> bool {
    object.len() == expected.len() && expected.iter().all(|key| object.contains_key(*key))
}

/// The release a manifest is checked against.
#[derive(Debug, Clone, Copy)]
pub struct Context<'a> {
    pub current: &'a Version,
    pub target: &'a str,
    pub distribution: &'a str,
    pub keyring: &'a KeyRing,
}

/// `parse_manifest(raw, current=, target=, distribution=, verify=)`.
pub fn parse_manifest(raw: &[u8], context: Context<'_>) -> CodedResult<Manifest> {
    parse(raw, context, false)
}

/// `parse_current_recovery_manifest(...)`: authenticate this exact running version for
/// retention; it is never offered as an update.
pub fn parse_current_recovery_manifest(raw: &[u8], context: Context<'_>) -> CodedResult<Manifest> {
    parse(raw, context, true)
}

fn parse(raw: &[u8], context: Context<'_>, current_recovery: bool) -> CodedResult<Manifest> {
    let envelope = bounded_json(raw, MANIFEST_LIMIT)?;
    let incomplete = || update_error("invalid_metadata", "The signed update envelope is incomplete.");
    let Json::Object(envelope) = envelope else {
        return Err(incomplete());
    };
    if !has_exactly(&envelope, &["signed", "key_id", "signature"]) {
        return Err(incomplete());
    }
    let malformed = || update_error("invalid_signature", "The update signature is missing or malformed.");
    let (Json::Object(signed), Json::String(key_id), Json::String(encoded)) =
        (&envelope["signed"], &envelope["key_id"], &envelope["signature"])
    else {
        return Err(malformed());
    };
    let key_id_valid = (1..=64).contains(&key_id.len())
        && key_id.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'));
    if !key_id_valid || encoded.chars().count() > 100 {
        return Err(malformed());
    }
    let signature = b64decode_strict(encoded).ok_or_else(malformed)?;
    if signature.len() != 64 {
        return Err(malformed());
    }
    let message = canonical_bytes(&Json::Object(signed.clone()));
    context.keyring.verify(key_id, &message, &signature)?;
    let fields = [
        "format_version",
        "version",
        "owner_version",
        "minimum_app_version",
        "channel",
        "target",
        "distribution",
        "notes",
        "artifact",
        "files",
    ];
    let format_ok =
        signed.get("format_version").is_some_and(|value| value.is_number() && value.as_i64() == Some(1));
    if !has_exactly(signed, &fields) || !format_ok {
        return Err(update_error("invalid_metadata", "The signed update manifest is incompatible."));
    }
    let version = Version::parse(&signed["version"])?;
    let minimum = Version::parse(&signed["minimum_app_version"])?;
    let current = context.current;
    if current_recovery && version.text != current.text {
        return Err(update_error(
            "wrong_recovery_version",
            "Recovery must retain the exact currently running release.",
        ));
    }
    if !current_recovery && version <= *current {
        return Err(update_error("not_newer", "This release is not newer than the current application."));
    }
    if minimum > *current || minimum > version {
        return Err(update_error(
            "bootstrap_required",
            "This release needs a newer manual bootstrap installation.",
        ));
    }
    if signed["owner_version"] != Json::String(version.text.clone()) {
        return Err(update_error(
            "wrong_component_version",
            "Desktop and owner versions must update together.",
        ));
    }
    // The running build declares its channel. A plain X.Y.Z version names no channel itself.
    if signed["channel"] != RELEASE_CHANNEL
        || (!version.prerelease.is_empty() && version.channel() != RELEASE_CHANNEL)
    {
        return Err(update_error("wrong_channel", "This release is for a different update channel."));
    }
    if signed["target"] != context.target || signed["distribution"] != context.distribution {
        return Err(update_error(
            "wrong_target",
            "This release is not for this architecture and distribution.",
        ));
    }
    let notes = match &signed["notes"] {
        Json::String(notes) if notes.len() <= 64 * 1024 => notes.clone(),
        _ => return Err(update_error("invalid_metadata", "Release notes are missing or too large.")),
    };
    let artifact = match &signed["artifact"] {
        Json::Object(artifact) if has_exactly(artifact, &["url", "size", "sha256"]) => artifact,
        _ => return Err(update_error("invalid_metadata", "The update artifact is incomplete.")),
    };
    let rows = match &signed["files"] {
        Json::Array(rows) if (3..=4096).contains(&rows.len()) => rows,
        _ => return Err(update_error("invalid_metadata", "The application file inventory is incomplete.")),
    };
    let mut files = Vec::with_capacity(rows.len());
    let mut names: HashSet<String> = HashSet::new();
    for row in rows {
        let row = match row {
            Json::Object(row) if has_exactly(row, &["path", "size", "sha256"]) => row,
            _ => return Err(update_error("invalid_metadata", "The application file inventory is invalid.")),
        };
        let name = payload_path(&row["path"])?;
        let folded = name.to_ascii_lowercase();
        if names.contains(&folded) {
            return Err(update_error("unsafe_payload", "The update contains duplicate application paths."));
        }
        names.insert(folded.clone());
        let maximum = if folded == "sylvia-portable.json" { 128 } else { ARTIFACT_LIMIT };
        let file_size = size(&row["size"], maximum)?;
        files.push(PayloadFile { path: name, size: file_size, sha256: content_hash(&row["sha256"])? });
    }
    if files.iter().map(|row| row.size).sum::<u64>() > EXPANDED_LIMIT {
        return Err(update_error("payload_limit", "The expanded update is too large."));
    }
    let required: Vec<String> = if context.distribution == "portable" {
        let mut names = required_payloads();
        names.extend(
            [ENTRY_MANIFEST, "BUILD-IDENTITY.json", "SOURCE-INPUTS.json"].into_iter().map(str::to_string),
        );
        names
    } else {
        let mut names = vec!["sylvia-gm.exe".to_string(), "sylvia-gm-owner.exe".to_string()];
        names.extend(LEGAL_FILES.iter().map(|name| format!("licenses/{name}")));
        names.extend(
            ["licenses/SRD-5.2.1.md", "licenses/Severed-Lands.md", "licenses/dependency-notices.txt"]
                .into_iter()
                .map(str::to_string),
        );
        names
    };
    if !required.iter().all(|name| names.contains(&name.to_ascii_lowercase())) {
        return Err(update_error(
            "invalid_metadata",
            "The update omits required application files or notices.",
        ));
    }
    if names.contains("sylvia-portable.json") != (context.distribution == "portable") {
        return Err(update_error("wrong_distribution", "The update would change this distribution mode."));
    }
    let artifact_url = release_url(&artifact["url"], &version.text, Some(context.distribution))?;
    let artifact_size = size(&artifact["size"], ARTIFACT_LIMIT)?;
    let artifact_sha256 = content_hash(&artifact["sha256"])?;
    Ok(Manifest {
        version,
        channel: RELEASE_CHANNEL.to_string(),
        target: context.target.to_string(),
        distribution: context.distribution.to_string(),
        notes,
        artifact_url,
        artifact_size,
        artifact_sha256,
        files,
        signed_bytes: message,
        envelope_bytes: raw.to_vec(),
    })
}

/// `canonical_bytes(value)`: sorted, compact, UTF-8. A `Json` value always encodes, so
/// Python's `invalid_metadata` failure cannot happen here.
pub fn canonical_bytes(value: &Json) -> Vec<u8> {
    pyjson::canonical_bytes(value)
}

/// Fails when an object has a repeated key, checked when the object closes as Python's
/// `object_pairs_hook` is, so a syntax error earlier in the same object wins.
struct UniqueKeys;

const DUPLICATE: &str = "sylvia: duplicate field";

impl<'de> Deserialize<'de> for UniqueKeys {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(UniqueKeysVisitor)
    }
}

struct UniqueKeysVisitor;

impl<'de> Visitor<'de> for UniqueKeysVisitor {
    type Value = UniqueKeys;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("JSON")
    }

    fn visit_bool<E>(self, _: bool) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_i64<E>(self, _: i64) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_u64<E>(self, _: u64) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_f64<E>(self, _: f64) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_str<E>(self, _: &str) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_unit<E>(self) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut items: A) -> Result<UniqueKeys, A::Error> {
        while items.next_element::<UniqueKeys>()?.is_some() {}
        Ok(UniqueKeys)
    }

    fn visit_map<A: MapAccess<'de>>(self, mut entries: A) -> Result<UniqueKeys, A::Error> {
        let mut keys: Vec<String> = Vec::new();
        while let Some(key) = entries.next_key::<String>()? {
            entries.next_value::<UniqueKeys>()?;
            keys.push(key);
        }
        let mut sorted = keys.clone();
        sorted.sort_unstable();
        if sorted.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(serde::de::Error::custom(DUPLICATE));
        }
        Ok(UniqueKeys)
    }
}

/// `bounded_json(raw, maximum=MANIFEST_LIMIT)`.
pub fn bounded_json(raw: &[u8], maximum: usize) -> CodedResult<Json> {
    if raw.is_empty() || raw.len() > maximum {
        return Err(update_error("metadata_limit", "Release metadata is missing or too large."));
    }
    let unreadable = || update_error("invalid_metadata", "Release metadata could not be read.");
    let text = std::str::from_utf8(raw).map_err(|_| unreadable())?;
    if let Err(error) = serde_json::from_str::<UniqueKeys>(text) {
        if error.to_string().starts_with(DUPLICATE) {
            return Err(update_error("invalid_metadata", "Release metadata contains duplicate fields."));
        }
        return Err(unreadable());
    }
    // Python's integer and `NaN` rules: see differences 1 and 2.
    let value = pyjson::loads(text).map_err(|_| unreadable())?;
    // The same walk as Python, last pushed first, so the same limit is met first.
    let mut pending: Vec<(&Json, usize)> = vec![(&value, 1)];
    let mut count = 0usize;
    while let Some((item, depth)) = pending.pop() {
        count += 1;
        if depth > 32 || count > 50_000 {
            return Err(update_error("metadata_limit", "Release metadata exceeds its structural limits."));
        }
        match item {
            Json::Object(map) => pending.extend(map.values().map(|child| (child, depth + 1))),
            Json::Array(items) => pending.extend(items.iter().map(|child| (child, depth + 1))),
            Json::Number(number) if !(number.is_i64() || number.is_u64()) => {
                return Err(update_error(
                    "invalid_metadata",
                    "Release metadata contains unsupported values.",
                ));
            }
            _ => {}
        }
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn code(raw: &str) -> String {
        bounded_json(raw.as_bytes(), MANIFEST_LIMIT).unwrap_err().code
    }

    #[test]
    fn refuses_what_python_refuses() {
        assert_eq!(
            bounded_json(b"", MANIFEST_LIMIT).unwrap_err().message,
            "Release metadata is missing or too large."
        );
        assert_eq!(
            bounded_json(br#"{"a":1,"a":2}"#, MANIFEST_LIMIT).unwrap_err().message,
            "Release metadata contains duplicate fields."
        );
        assert_eq!(
            bounded_json(br#"{"a":1.5}"#, MANIFEST_LIMIT).unwrap_err().message,
            "Release metadata contains unsupported values."
        );
        assert_eq!(code("{"), "invalid_metadata");
        let deep = format!("{}{}", "[".repeat(33), "]".repeat(33));
        assert_eq!(bounded_json(deep.as_bytes(), MANIFEST_LIMIT).unwrap_err().code, "metadata_limit");
        let shallow = format!("{}{}", "[".repeat(32), "]".repeat(32));
        assert!(bounded_json(shallow.as_bytes(), MANIFEST_LIMIT).is_ok());
        assert_eq!(bounded_json(br#"{"a":[1,"x",true,null]}"#, MANIFEST_LIMIT).unwrap()["a"][1], "x");
    }
}
