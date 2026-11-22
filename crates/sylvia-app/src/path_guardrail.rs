//! `builder/_guardrail.py`: keep Builder file operations inside one allowed root directory.
//!
//! Python resolves paths with `Path.resolve()`, whose behaviour is the platform's:
//! `ntpath.realpath` on Windows (normalize `..` lexically, then the final path name of the longest
//! existing prefix, with `\\?\` stripped) and `posixpath.realpath` on Linux (symbolic links
//! resolved component by component, before `..`). `relative_to` compares case-insensitively on
//! Windows only. Both are reproduced here, per platform, because the resolved path appears in the
//! model-facing refusal text.

use std::fmt;
use std::path::{Path, PathBuf};

/// `GuardrailBlockedError`: a path the guardrail refuses.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GuardrailBlockedError {
    pub raw_path: String,
    pub resolved: String,
    pub allowed_root: String,
    pub reason: String,
}

impl fmt::Display for GuardrailBlockedError {
    /// `str(error)`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "Access denied: '{}' resolves to '{}', which is outside the project directory '{}'. The Builder agent can \
             only access files within the Sylvia GM project.",
            self.raw_path, self.resolved, self.allowed_root
        )?;
        if !self.reason.is_empty() {
            write!(f, " Reason: {}", self.reason)?;
        }
        Ok(())
    }
}

impl std::error::Error for GuardrailBlockedError {}

/// `PathGuardrail`: validates file paths are within an allowed root directory.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PathGuardrail {
    allowed_root: String,
}

/// `WRITE_DENIED_PATTERNS`.
const WRITE_DENIED_PATTERNS: [&str; 3] = [".env", ".key", ".pem"];
/// `READ_ONLY_ROOTS`.
const READ_ONLY_ROOTS: [&str; 1] = [".builder-sources"];
/// `INTERNAL_ROOTS`.
const INTERNAL_ROOTS: [&str; 1] = [".builder-sources-purge-pending"];

/// The message `Path.resolve()` raises for a path containing NUL, on this platform.
#[cfg(windows)]
const EMBEDDED_NULL: &str = "stat: embedded null character in path";
#[cfg(not(windows))]
const EMBEDDED_NULL: &str = "embedded null byte";

impl PathGuardrail {
    /// `PathGuardrail(allowed_root)`: the root, resolved.
    pub fn new(allowed_root: &Path) -> Self {
        let text = allowed_root.to_string_lossy().into_owned();
        let absolute =
            if platform::is_absolute(&text) { text } else { platform::join(&platform::cwd(), &text) };
        Self { allowed_root: platform::resolve(&absolute) }
    }

    /// `allowed_root`, as `str(Path)` writes it.
    pub fn allowed_root(&self) -> &str {
        &self.allowed_root
    }

    fn blocked(&self, raw_path: &str, resolved: &str, reason: &str) -> GuardrailBlockedError {
        GuardrailBlockedError {
            raw_path: raw_path.to_owned(),
            resolved: resolved.to_owned(),
            allowed_root: self.allowed_root.clone(),
            reason: reason.to_owned(),
        }
    }

    /// `validate(raw_path, for_write=...)`: the resolved path, or the refusal.
    pub fn validate(&self, raw_path: &str, for_write: bool) -> Result<PathBuf, GuardrailBlockedError> {
        if raw_path.contains('\0') {
            // `Path.resolve()` raises `ValueError`; the guardrail reports `Path(raw_path)`.
            return Err(self.blocked(raw_path, &platform::display(raw_path), EMBEDDED_NULL));
        }
        let joined = if platform::is_absolute(raw_path) {
            raw_path.to_owned()
        } else {
            platform::join(&self.allowed_root, raw_path)
        };
        let resolved = platform::resolve(&joined);
        let Some(relative) = platform::relative_parts(&resolved, &self.allowed_root) else {
            return Err(self.blocked(raw_path, &resolved, ""));
        };
        let top_level = relative.first().map(|part| sylvia_base::pystr::casefold(part)).unwrap_or_default();
        if INTERNAL_ROOTS.contains(&top_level.as_str()) {
            return Err(self.blocked(
                raw_path,
                &resolved,
                "This owner-managed lifecycle path is not available to Builder tools.",
            ));
        }
        if for_write && READ_ONLY_ROOTS.contains(&top_level.as_str()) {
            return Err(self.blocked(raw_path, &resolved, "Attached Builder sources are read-only."));
        }
        if for_write {
            let name = platform::name(&resolved);
            if WRITE_DENIED_PATTERNS
                .iter()
                .any(|denied| name == *denied || name.starts_with(denied) || name.ends_with(denied))
            {
                return Err(self.blocked(
                    raw_path,
                    &resolved,
                    &format!("Write access to '{name}' files is blocked for security."),
                ));
            }
            // `resolved.suffix in (".key", ".pem")` is already covered by `endswith` above.
        }
        Ok(PathBuf::from(resolved))
    }
}

#[cfg(windows)]
pub(crate) mod platform {
    //! `ntpath` and `PureWindowsPath`.

    fn is_sep(c: char) -> bool {
        c == '\\' || c == '/'
    }

    /// The drive (`C:` or `\\server\share`) and the rest.
    fn split_drive(path: &str) -> (&str, &str) {
        let chars: Vec<char> = path.chars().collect();
        if chars.len() >= 2
            && is_sep(chars[0])
            && is_sep(chars[1])
            && chars.get(2).is_some_and(|c| !is_sep(*c))
        {
            // UNC: \\server\share
            let rest = &path[2..];
            let Some(server_end) = rest.find(is_sep) else { return (path, "") };
            let after_server = &rest[server_end + 1..];
            let share_end = after_server.find(is_sep).unwrap_or(after_server.len());
            let drive_len = 2 + server_end + 1 + share_end;
            return (&path[..drive_len], &path[drive_len..]);
        }
        if chars.len() >= 2 && chars[1] == ':' && chars[0].is_ascii_alphabetic() {
            return (&path[..2], &path[2..]);
        }
        ("", path)
    }

    /// `PureWindowsPath.is_absolute()`: a drive and a root.
    pub fn is_absolute(path: &str) -> bool {
        let (drive, rest) = split_drive(path);
        !drive.is_empty()
            && (rest.starts_with(is_sep) || drive.starts_with("\\\\") || drive.starts_with("//"))
    }

    /// `PureWindowsPath(base) / other`.
    pub fn join(base: &str, other: &str) -> String {
        let (other_drive, other_rest) = split_drive(other);
        let (base_drive, _) = split_drive(base);
        if !other_drive.is_empty() && !other_drive.eq_ignore_ascii_case(base_drive) {
            return other.to_owned();
        }
        if other_rest.starts_with(is_sep) {
            return format!("{base_drive}{other_rest}");
        }
        if other_rest.is_empty() {
            return base.to_owned();
        }
        format!("{}\\{other_rest}", base.trim_end_matches(is_sep))
    }

    /// `ntpath.normpath`: separators to `\`, empty and `.` parts dropped, `..` collapsed.
    pub fn normpath(path: &str) -> String {
        let (drive, rest) = split_drive(path);
        let drive = drive.replace('/', "\\");
        let rooted = rest.starts_with(is_sep);
        let mut parts: Vec<&str> = Vec::new();
        for part in rest.split(is_sep) {
            match part {
                "" | "." => {}
                ".." => {
                    if parts.last().is_some_and(|last| *last != "..") {
                        parts.pop();
                    } else if !rooted {
                        parts.push("..");
                    }
                }
                other => parts.push(other),
            }
        }
        let prefix = if rooted { format!("{drive}\\") } else { drive };
        let joined = format!("{prefix}{}", parts.join("\\"));
        if joined.is_empty() {
            ".".into()
        } else {
            joined
        }
    }

    /// `_getfinalpathname(path)` with the `\\?\` prefix stripped, as `realpath` strips it.
    fn final_path_name(path: &str) -> Option<String> {
        let canonical = std::fs::canonicalize(path).ok()?;
        let text = canonical.to_string_lossy().into_owned();
        Some(if let Some(unc) = text.strip_prefix("\\\\?\\UNC\\") {
            format!("\\\\{unc}")
        } else if let Some(rest) = text.strip_prefix("\\\\?\\") {
            rest.to_owned()
        } else {
            text
        })
    }

    /// `ntpath.realpath(path)` (non-strict): the final path name of the longest existing prefix,
    /// with the rest of the normalized path joined on.
    pub fn resolve(path: &str) -> String {
        let mut head = normpath(path);
        let mut tail: Vec<String> = Vec::new();
        loop {
            if let Some(found) = final_path_name(&head) {
                if tail.is_empty() {
                    return found;
                }
                tail.reverse();
                return format!("{}\\{}", found.trim_end_matches('\\'), tail.join("\\"));
            }
            let (drive, rest) = split_drive(&head);
            let trimmed = rest.trim_end_matches('\\');
            match trimmed.rfind('\\') {
                Some(at) if !trimmed[at + 1..].is_empty() => {
                    tail.push(trimmed[at + 1..].to_owned());
                    head = format!("{drive}{}", if at == 0 { "\\" } else { &trimmed[..at] });
                }
                _ => {
                    if !trimmed.is_empty() && !trimmed.contains('\\') {
                        tail.push(trimmed.to_owned());
                        head = drive.to_owned();
                        if head.is_empty() {
                            tail.reverse();
                            return tail.join("\\");
                        }
                        continue;
                    }
                    tail.reverse();
                    return if tail.is_empty() {
                        head
                    } else {
                        format!("{}\\{}", head.trim_end_matches('\\'), tail.join("\\"))
                    };
                }
            }
        }
    }

    /// `resolved.relative_to(root).parts`, compared case-insensitively as Windows paths are.
    pub fn relative_parts(resolved: &str, root: &str) -> Option<Vec<String>> {
        let parts = |path: &str| -> Vec<String> {
            let (drive, rest) = split_drive(path);
            let mut parts = vec![drive.to_lowercase()];
            parts.extend(rest.split('\\').filter(|part| !part.is_empty()).map(str::to_owned));
            parts
        };
        let (resolved_parts, root_parts) = (parts(resolved), parts(root));
        if resolved_parts.len() < root_parts.len() {
            return None;
        }
        let matches =
            resolved_parts.iter().zip(&root_parts).all(|(a, b)| a.to_lowercase() == b.to_lowercase());
        matches.then(|| resolved_parts[root_parts.len()..].to_vec())
    }

    /// `PureWindowsPath(path).name`.
    pub fn name(path: &str) -> String {
        let (_, rest) = split_drive(path);
        rest.rsplit(is_sep).next().unwrap_or_default().to_owned()
    }

    /// `str(Path(raw))`.
    pub fn display(raw: &str) -> String {
        let (drive, rest) = split_drive(raw);
        let parts: Vec<&str> = rest.split(is_sep).filter(|part| !part.is_empty() && *part != ".").collect();
        let root = if rest.starts_with(is_sep) { "\\" } else { "" };
        let text = format!("{}{root}{}", drive.replace('/', "\\"), parts.join("\\"));
        if text.is_empty() {
            ".".into()
        } else {
            text
        }
    }

    pub fn cwd() -> String {
        std::env::current_dir().map(|dir| dir.to_string_lossy().into_owned()).unwrap_or_default()
    }
}

#[cfg(not(windows))]
pub(crate) mod platform {
    //! `posixpath` and `PurePosixPath`.

    use std::collections::HashMap;

    pub fn is_absolute(path: &str) -> bool {
        path.starts_with('/')
    }

    /// `PurePosixPath(base) / other`.
    pub fn join(base: &str, other: &str) -> String {
        if other.starts_with('/') {
            other.to_owned()
        } else if other.is_empty() {
            base.to_owned()
        } else {
            format!("{}/{other}", base.trim_end_matches('/'))
        }
    }

    fn split(path: &str) -> (String, String) {
        match path.rfind('/') {
            Some(at) => {
                let head = &path[..at + 1];
                let head = if head.chars().all(|c| c == '/') {
                    head.to_owned()
                } else {
                    head.trim_end_matches('/').to_owned()
                };
                (head, path[at + 1..].to_owned())
            }
            None => (String::new(), path.to_owned()),
        }
    }

    fn join_name(path: &str, name: &str) -> String {
        if path.is_empty() {
            name.to_owned()
        } else if path.ends_with('/') {
            format!("{path}{name}")
        } else {
            format!("{path}/{name}")
        }
    }

    /// `posixpath._joinrealpath(path, rest, strict=False, seen)`.
    fn join_real(mut path: String, rest: &str, seen: &mut HashMap<String, Option<String>>) -> (String, bool) {
        let mut rest = rest.to_owned();
        if rest.starts_with('/') {
            rest = rest[1..].to_owned();
            path = "/".into();
        }
        while !rest.is_empty() {
            let (name, remainder) = match rest.find('/') {
                Some(at) => (rest[..at].to_owned(), rest[at + 1..].to_owned()),
                None => (rest.clone(), String::new()),
            };
            rest = remainder;
            if name.is_empty() || name == "." {
                continue;
            }
            if name == ".." {
                if path.is_empty() {
                    path = "..".into();
                } else {
                    let (head, tail) = split(&path);
                    path = if tail == ".." { join_name(&join_name(&head, ".."), "..") } else { head };
                }
                continue;
            }
            let new_path = join_name(&path, &name);
            let is_link =
                std::fs::symlink_metadata(&new_path).map(|m| m.file_type().is_symlink()).unwrap_or(false);
            if !is_link {
                path = new_path;
                continue;
            }
            if let Some(known) = seen.get(&new_path) {
                match known {
                    Some(resolved) => {
                        path = resolved.clone();
                        continue;
                    }
                    None => return (join_name(&new_path, &rest), false),
                }
            }
            seen.insert(new_path.clone(), None);
            let target =
                std::fs::read_link(&new_path).map(|t| t.to_string_lossy().into_owned()).unwrap_or_default();
            let (resolved, ok) = join_real(path.clone(), &target, seen);
            if !ok {
                return (join_name(&resolved, &rest), false);
            }
            seen.insert(new_path, Some(resolved.clone()));
            path = resolved;
        }
        (path, true)
    }

    /// `posixpath.normpath` as `abspath` applies it.
    fn normpath(path: &str) -> String {
        let rooted = path.starts_with('/');
        let double = path.starts_with("//") && !path.starts_with("///");
        let mut parts: Vec<&str> = Vec::new();
        for part in path.split('/') {
            match part {
                "" | "." => {}
                ".." => {
                    if parts.last().is_some_and(|last| *last != "..") {
                        parts.pop();
                    } else if !rooted {
                        parts.push("..");
                    }
                }
                other => parts.push(other),
            }
        }
        let prefix = if double {
            "//"
        } else if rooted {
            "/"
        } else {
            ""
        };
        let joined = format!("{prefix}{}", parts.join("/"));
        if joined.is_empty() {
            ".".into()
        } else {
            joined
        }
    }

    /// `posixpath.realpath(path)` (non-strict).
    pub fn resolve(path: &str) -> String {
        let (resolved, _) = join_real(String::new(), path, &mut HashMap::new());
        let absolute = if resolved.starts_with('/') { resolved } else { join(&cwd(), &resolved) };
        normpath(&absolute)
    }

    /// `resolved.relative_to(root).parts`.
    pub fn relative_parts(resolved: &str, root: &str) -> Option<Vec<String>> {
        let parts = |path: &str| -> Vec<String> {
            path.split('/').filter(|p| !p.is_empty()).map(str::to_owned).collect()
        };
        let (resolved_parts, root_parts) = (parts(resolved), parts(root));
        (resolved_parts.len() >= root_parts.len() && resolved_parts[..root_parts.len()] == root_parts[..])
            .then(|| resolved_parts[root_parts.len()..].to_vec())
    }

    pub fn name(path: &str) -> String {
        path.rsplit('/').next().unwrap_or_default().to_owned()
    }

    /// `str(Path(raw))`.
    pub fn display(raw: &str) -> String {
        let parts: Vec<&str> = raw.split('/').filter(|part| !part.is_empty() && *part != ".").collect();
        let root = if raw.starts_with("//") && !raw.starts_with("///") {
            "//"
        } else if raw.starts_with('/') {
            "/"
        } else {
            ""
        };
        let text = format!("{root}{}", parts.join("/"));
        if text.is_empty() {
            ".".into()
        } else {
            text
        }
    }

    pub fn cwd() -> String {
        std::env::current_dir().map(|dir| dir.to_string_lossy().into_owned()).unwrap_or_default()
    }
}
