//! The loopback WebUI host: `interfaces/webui.py`.
//!
//! The browser receives projections, never an owner credential or filesystem API. The host
//! starts its own owner process over the same private pipes the desktop uses ([`owner`]) and
//! serves the unchanged frontend plus a JSON command endpoint ([`host`]). The child keeps all
//! campaign admission, stream leases, recovery and persistence.

pub mod host;
pub mod owner;

use crate::commands::REGISTRY;
use regex::Regex;
use sha2::{Digest, Sha256};
use std::path::Path;
use std::sync::OnceLock;
use sylvia_base::{pystr, Json, JsonMap};

/// `MAX_BYTES`: the largest command request or owner reply line.
pub const MAX_BYTES: usize = 64 * 1024 * 1024;
/// `MAX_UPLOAD_BYTES`.
pub const MAX_UPLOAD_BYTES: usize = 48 * 1024 * 1024;
/// `MAX_UPLOAD_PARAMS_BYTES`.
pub const MAX_UPLOAD_PARAMS_BYTES: usize = 16 * 1024;
/// `COOKIE`: the local cookie name, followed by `_<port>`.
pub const COOKIE: &str = "sylvia_webui";
/// `REMOTE_COOKIE`.
pub const REMOTE_COOKIE: &str = "__Host-sylvia_webui";
/// `REMOTE_SESSION_SECONDS`.
pub const REMOTE_SESSION_SECONDS: u64 = 12 * 60 * 60;
/// `OWNER_EXIT_TIMEOUT`, in seconds.
pub const OWNER_EXIT_TIMEOUT: u64 = 15;

/// `EXPORTS`: the path-taking export commands the host answers with a download instead.
pub const EXPORTS: &[(&str, &str, &str)] = &[
    ("export_campaign_file", "exports", ".sylvia-gm-campaign"),
    ("export_package_candidate_file", "exports", ".sylvia-gm-package"),
    ("export_campaign_plan_file", "exports", ".sylvia-gm-plan"),
    ("backup_authority", "backups", ".sqlite3"),
];

/// `WRAPPED`: commands whose frontend arguments nest the owner's under `command` or `request`.
pub const WRAPPED: &[&str] = &[
    "start_ready_campaign",
    "customize_ready_campaign",
    "compose_action_sequence",
    "execute_action_sequence",
    "campaign_sequel_context",
    "preview_campaign_sequel",
    "append_campaign_sequel",
];

/// `COMMANDS` (`WEBUI_COMMANDS`): the owner commands the browser may call.
pub fn is_webui_command(method: &str) -> bool {
    REGISTRY.iter().any(|command| command.webui && command.name == method)
}

/// `WebUIError(message, code)`: a safe error for the browser or the launch terminal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebUIError {
    /// The browser-facing code.
    pub code: String,
    /// `str(error)`.
    pub message: String,
}

impl WebUIError {
    /// `WebUIError(message, code)`.
    pub fn new(message: impl Into<String>, code: &str) -> Self {
        Self { code: code.to_owned(), message: message.into() }
    }

    /// `WebUIError(message)`: the default code `webui_unavailable`.
    pub fn unavailable(message: impl Into<String>) -> Self {
        Self::new(message, "webui_unavailable")
    }
}

impl std::fmt::Display for WebUIError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

fn pattern(cell: &'static OnceLock<Regex>, text: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(text).expect("static pattern"))
}

/// `RemoteAccess`: an explicit tailnet HTTPS origin and the SHA-256 of the user's access key.
#[derive(Clone, PartialEq, Eq)]
pub struct RemoteAccess {
    /// The exact origin, for example `https://node.tailnet.ts.net`.
    pub origin: String,
    /// `sha256(key).digest()`.
    pub key_digest: [u8; 32],
}

impl std::fmt::Debug for RemoteAccess {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.debug_struct("RemoteAccess").field("origin", &self.origin).finish_non_exhaustive()
    }
}

impl RemoteAccess {
    /// `RemoteAccess.from_file(origin, key_file)`.
    pub fn from_file(origin: &str, key_file: &Path) -> Result<Self, WebUIError> {
        static ORIGIN: OnceLock<Regex> = OnceLock::new();
        let origin_pattern = pattern(
            &ORIGIN,
            r"^https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2,}ts\.net(?::[1-9][0-9]{0,4})?$",
        );
        // `re.fullmatch`: `$` here may not match before a final newline.
        if origin.ends_with('\n') || !origin_pattern.is_match(origin) {
            return Err(WebUIError::unavailable(
                "Remote origin must be one exact lowercase https://node.tailnet.ts.net origin, \
                 with an optional port and no path.",
            ));
        }
        let authority = &origin["https://".len()..];
        let (hostname, port) = match authority.rsplit_once(':') {
            Some((hostname, port)) => (hostname, Some(port)),
            None => (authority, None),
        };
        // `urlsplit(origin).port` raises ValueError above 65535.
        let port = match port {
            Some(text) => match text.parse::<u32>() {
                Ok(value) if value <= 65535 => Some(value),
                _ => return Err(WebUIError::unavailable("Remote HTTPS port is invalid.")),
            },
            None => None,
        };
        if port == Some(443) || hostname.chars().count() > 253 {
            return Err(WebUIError::unavailable(
                "Use the canonical HTTPS origin (omit the default :443 port).",
            ));
        }
        let raw = read_prefix(key_file, 131)
            .map_err(|_| WebUIError::unavailable("Cannot read the selected WebUI access-key file."))?;
        let key = match raw.strip_suffix(b"\r\n").or_else(|| raw.strip_suffix(b"\n")) {
            Some(key) => key,
            None => &raw[..],
        };
        let valid = (43..=128).contains(&key.len())
            && key.iter().all(|byte| byte.is_ascii_alphanumeric() || *byte == b'_' || *byte == b'-');
        if !valid {
            return Err(WebUIError::unavailable(
                "The access-key file must contain 43 to 128 random URL-safe characters, with an \
                 optional final newline.",
            ));
        }
        Ok(Self { origin: origin.to_owned(), key_digest: Sha256::digest(key).into() })
    }
}

fn read_prefix(path: &Path, limit: u64) -> std::io::Result<Vec<u8>> {
    use std::io::Read;
    let mut raw = Vec::new();
    std::fs::File::open(path)?.take(limit).read_to_end(&mut raw)?;
    Ok(raw)
}

/// `_host_platform()`: the system this owner runs on, named as the desktop shell names it.
pub fn host_platform() -> &'static str {
    if cfg!(windows) {
        "windows"
    } else if cfg!(target_os = "linux") {
        "linux"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        std::env::consts::OS
    }
}

/// `re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()`: camelCase to snake_case.
pub fn snake_case(key: &str) -> String {
    let mut out = String::with_capacity(key.len() + 4);
    for (index, character) in key.chars().enumerate() {
        if index > 0 && character.is_ascii_uppercase() {
            out.push('_');
        }
        out.push(character);
    }
    // `str.lower()` is Unicode-aware, as `to_lowercase` is.
    out.to_lowercase()
}

/// `browser_command(method, raw)`: adapt the frontend's native argument names without
/// rewriting game payloads.
pub fn browser_command(method: &str, raw: &Json) -> Result<(String, JsonMap), WebUIError> {
    let mut raw = raw;
    if WRAPPED.contains(&method) {
        if let Some(map) = raw.as_object() {
            raw = map.get("command").or_else(|| map.get("request")).unwrap_or(raw);
        }
    }
    let Some(map) = raw.as_object() else {
        return Err(WebUIError::new("Command parameters must be an object.", "invalid_request"));
    };
    let mut params = JsonMap::new();
    for (key, value) in map {
        params.insert(snake_case(key), value.clone());
    }
    if ["source_path", "source_paths", "destination_path", "database_path", "host"]
        .iter()
        .any(|key| params.contains_key(*key))
    {
        return Err(WebUIError::new("Host file paths are unavailable in the browser.", "unsupported"));
    }
    if method == "resolve_mechanics_choice" && !params.contains_key("choice_sequence") {
        params.insert("choice_sequence".into(), 0.into());
    }
    Ok((method.to_owned(), params))
}

/// A command identity, an export identity or a backup identity: `[A-Za-z0-9_-]{1,128}`.
pub fn is_identity(text: &str) -> bool {
    (1..=128).contains(&text.len())
        && text.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}

/// `secrets.token_urlsafe(nbytes)`.
pub fn token_urlsafe(nbytes: usize) -> String {
    let mut bytes = vec![0u8; nbytes];
    getrandom::getrandom(&mut bytes).expect("the operating system's random source is available");
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let value = chunk
            .iter()
            .enumerate()
            .fold(0u32, |acc, (index, byte)| acc | (u32::from(*byte) << (16 - 8 * index)));
        let characters = chunk.len() + 1;
        for index in 0..characters {
            out.push(ALPHABET[((value >> (18 - 6 * index)) & 63) as usize] as char);
        }
    }
    out
}

/// `secrets.token_hex(nbytes)`.
pub fn token_hex(nbytes: usize) -> String {
    let mut bytes = vec![0u8; nbytes];
    getrandom::getrandom(&mut bytes).expect("the operating system's random source is available");
    hex::encode(bytes)
}

/// `Path(name).is_reserved()` for a selected upload's name: Windows device names, checked only
/// on Windows (a POSIX path is never reserved).
pub fn is_reserved_name(name: &str) -> bool {
    if !cfg!(windows) || name.is_empty() {
        return false;
    }
    let stem = name.split('.').next().unwrap_or("");
    let stem = stem.split(':').next().unwrap_or("").trim_end_matches(' ');
    let upper = pystr::upper(stem);
    matches!(upper.as_str(), "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$")
        || ["COM", "LPT"].iter().any(|prefix| {
            upper.strip_prefix(prefix).is_some_and(|rest| {
                let mut characters = rest.chars();
                matches!(
                    (characters.next(), characters.next()),
                    (Some('1'..='9' | '\u{b9}' | '\u{b2}' | '\u{b3}'), None)
                )
            })
        })
}

/// The selected-file name checks of `LocalWebUI.upload`.
pub fn is_valid_upload_name(name: &str) -> bool {
    // `Path(name).name != name`: a separator (and on Windows a drive) makes them differ.
    let separators: &[char] = if cfg!(windows) { &['/', '\\'] } else { &['/'] };
    let plain = !name.contains(separators) && !(cfg!(windows) && name.chars().nth(1) == Some(':'));
    plain
        && name != "."
        && name != ".."
        && !name.is_empty()
        && !is_reserved_name(name)
        && name.chars().count() <= 180
        && !name.chars().any(|c| "\\/:\x00<>|?*\"".contains(c) || (c as u32) < 32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn renames_arguments_like_python() {
        assert_eq!(snake_case("sessionId"), "session_id");
        assert_eq!(snake_case("URLPath"), "u_r_l_path");
        assert_eq!(snake_case("already_snake"), "already_snake");
        let (_, params) =
            browser_command("start_ready_campaign", &json!({"command": {"operationId": "x"}})).unwrap();
        assert_eq!(params, json!({"operation_id": "x"}).as_object().cloned().unwrap());
        let (_, params) = browser_command("resolve_mechanics_choice", &json!({})).unwrap();
        assert_eq!(params["choice_sequence"], 0);
        assert_eq!(browser_command("x", &json!({"sourcePath": "a"})).unwrap_err().code, "unsupported");
        assert_eq!(
            browser_command("x", &json!([1])).unwrap_err().message,
            "Command parameters must be an object."
        );
    }

    #[test]
    fn tokens_have_pythons_lengths() {
        assert_eq!(token_urlsafe(32).len(), 43);
        assert_eq!(token_urlsafe(24).len(), 32);
        assert_eq!(token_hex(16).len(), 32);
    }

    #[test]
    fn upload_names_are_checked_like_python() {
        assert!(is_valid_upload_name("campaign.sylvia-gm-campaign"));
        for name in ["../a.zip", "a/b", "a:b", "", "..", "a\u{1}b", &"a".repeat(181)] {
            assert!(!is_valid_upload_name(name), "{name:?}");
        }
        if cfg!(windows) {
            assert!(!is_valid_upload_name("CON"));
            assert!(!is_valid_upload_name("com1.txt"));
            assert!(is_valid_upload_name("COM10"));
        }
    }

    #[test]
    fn remote_origins_follow_pythons_rules() {
        let folder = std::env::temp_dir().join(format!("sylvia-webui-key-{}", std::process::id()));
        std::fs::create_dir_all(&folder).unwrap();
        let key = folder.join("key");
        std::fs::write(&key, format!("{}\n", "k".repeat(43))).unwrap();
        assert!(RemoteAccess::from_file("https://node.tail1.ts.net", &key).is_ok());
        assert_eq!(
            RemoteAccess::from_file("https://node.tail1.ts.net:443", &key).unwrap_err().message,
            "Use the canonical HTTPS origin (omit the default :443 port)."
        );
        assert_eq!(
            RemoteAccess::from_file("https://node.tail1.ts.net:99999", &key).unwrap_err().message,
            "Remote HTTPS port is invalid."
        );
        assert!(RemoteAccess::from_file("https://Node.tail1.ts.net", &key).is_err());
        std::fs::write(&key, "short").unwrap();
        assert!(RemoteAccess::from_file("https://node.tail1.ts.net", &key)
            .unwrap_err()
            .message
            .starts_with("The access-key file"));
        let _ = std::fs::remove_dir_all(folder);
    }
}
