//! `httpx.URL` as httpx 0.28 parses and normalises it (`httpx/_urlparse.py`), for the URL the
//! OpenAI SDK sends a request to.
//!
//! The SDK takes the configured base URL through `httpx.URL`, adds a trailing slash to its raw
//! path, then appends the endpoint (`chat/completions`) to that raw path. So a query in the base
//! URL swallows the endpoint (`http://h/v1?key=1` sends to `http://h/v1?key=1/chat/completions`),
//! a fragment is kept, default ports are dropped, the host is lowercased and the path is
//! percent-encoded with httpx's sets.
//!
//! Not ported: IDNA encoding of a non-ASCII host name (httpx uses the `idna` package). Such a
//! host is refused here with httpx's own message for an invalid IDNA host.

/// `httpx.InvalidURL` with its message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvalidUrl(pub String);

/// A parsed URL in httpx's canonical form.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpxUrl {
    pub scheme: String,
    pub userinfo: String,
    pub host: String,
    pub port: Option<u32>,
    pub path: String,
    pub query: Option<String>,
    pub fragment: Option<String>,
}

const MAX_URL_LENGTH: usize = 65_536;
const UNRESERVED: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
const SUB_DELIMS: &str = "!$&'()*+,;=";

fn printable_except(excluded: &[u8]) -> String {
    (0x20u8..0x7f).filter(|byte| !excluded.contains(byte)).map(char::from).collect()
}

fn frag_safe() -> String {
    printable_except(&[0x20, 0x22, 0x3c, 0x3e, 0x60])
}

fn query_safe() -> String {
    printable_except(&[0x20, 0x22, 0x23, 0x3c, 0x3e])
}

fn path_safe() -> String {
    printable_except(&[0x20, 0x22, 0x23, 0x3c, 0x3e, 0x3f, 0x60, 0x7b, 0x7d])
}

fn userinfo_safe() -> String {
    printable_except(&[
        0x20, 0x22, 0x23, 0x3c, 0x3e, 0x3f, 0x60, 0x7b, 0x7d, 0x2f, 0x3b, 0x3d, 0x40, 0x5b, 0x5c, 0x5d, 0x5e,
        0x7c,
    ])
}

/// `percent_encoded(string, safe)`: UTF-8 bytes of each unsafe character as `%XX`.
fn percent_encoded(text: &str, safe: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for character in text.chars() {
        if UNRESERVED.contains(character) || safe.contains(character) {
            out.push(character);
        } else {
            let mut buffer = [0u8; 4];
            for byte in character.encode_utf8(&mut buffer).bytes() {
                out.push_str(&format!("%{byte:02X}"));
            }
        }
    }
    out
}

/// `quote(string, safe)`: percent-encode, keeping existing `%XX` escapes.
fn quote(text: &str, safe: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut pending = String::new();
    let mut index = 0;
    while index < chars.len() {
        let escape = chars[index] == '%'
            && chars.get(index + 1).is_some_and(char::is_ascii_hexdigit)
            && chars.get(index + 2).is_some_and(char::is_ascii_hexdigit);
        if escape {
            out.push_str(&percent_encoded(&pending, safe));
            pending.clear();
            out.extend(&chars[index..index + 3]);
            index += 3;
        } else {
            pending.push(chars[index]);
            index += 1;
        }
    }
    out.push_str(&percent_encoded(&pending, safe));
    out
}

fn check_printable(text: &str, component: Option<&str>) -> Result<(), InvalidUrl> {
    if let Some((index, character)) =
        text.chars().enumerate().find(|(_, c)| c.is_ascii() && (c.is_ascii_control()))
    {
        let shown = crate::pyobj::py_repr_str(&character.to_string());
        return Err(InvalidUrl(match component {
            None => format!("Invalid non-printable ASCII character in URL, {shown} at position {index}."),
            Some(key) => {
                format!("Invalid non-printable ASCII character in URL {key} component, {shown} at position {index}.")
            }
        }));
    }
    Ok(())
}

/// `URL_REGEX`: scheme, authority, path, query and fragment.
fn split_reference(url: &str) -> (String, String, String, Option<String>, Option<String>) {
    let mut rest = url;
    let mut scheme = String::new();
    // (?:(?P<scheme>([a-zA-Z][a-zA-Z0-9+.-]*)?):)?
    if let Some(colon) = rest.find(':') {
        let candidate = &rest[..colon];
        let valid = candidate.is_empty()
            || (candidate.starts_with(|c: char| c.is_ascii_alphabetic())
                && candidate.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '.' | '-')));
        // The scheme group cannot contain '/', '?' or '#', so a colon after them is not a scheme.
        if valid && !candidate.contains(['/', '?', '#']) {
            scheme = candidate.to_owned();
            rest = &rest[colon + 1..];
        }
    }
    let mut authority = String::new();
    if let Some(after) = rest.strip_prefix("//") {
        let end = after.find(['/', '?', '#']).unwrap_or(after.len());
        authority = after[..end].to_owned();
        rest = &after[end..];
    }
    let path_end = rest.find(['?', '#']).unwrap_or(rest.len());
    let path = rest[..path_end].to_owned();
    rest = &rest[path_end..];
    let mut query = None;
    if let Some(after) = rest.strip_prefix('?') {
        let end = after.find('#').unwrap_or(after.len());
        query = Some(after[..end].to_owned());
        rest = &after[end..];
    }
    let fragment = rest.strip_prefix('#').map(str::to_owned);
    (scheme, authority, path, query, fragment)
}

/// `AUTHORITY_REGEX`: userinfo, host (bracketed or up to `:`/`@`), port.
fn split_authority(authority: &str) -> (String, String, Option<String>) {
    // (?:(?P<userinfo>.*)@)? is greedy: the last '@' that still lets the rest match.
    let (userinfo, hostport) = match authority.rfind('@') {
        Some(at) => (authority[..at].to_owned(), &authority[at + 1..]),
        None => (String::new(), authority),
    };
    let (host, rest) = if hostport.starts_with('[') {
        match hostport.rfind(']') {
            Some(close) => (hostport[..=close].to_owned(), &hostport[close + 1..]),
            None => {
                let end = hostport.find([':', '@']).unwrap_or(hostport.len());
                (hostport[..end].to_owned(), &hostport[end..])
            }
        }
    } else {
        let end = hostport.find([':', '@']).unwrap_or(hostport.len());
        (hostport[..end].to_owned(), &hostport[end..])
    };
    let port = rest.strip_prefix(':').unwrap_or(rest);
    let port = if rest.is_empty() { None } else { Some(port.to_owned()) };
    (userinfo, host, port)
}

fn encode_host(host: &str) -> Result<String, InvalidUrl> {
    if host.is_empty() {
        return Ok(String::new());
    }
    let ipv4_style = {
        let parts: Vec<&str> = host.split('.').collect();
        parts.len() == 4
            && parts.iter().all(|part| !part.is_empty() && part.chars().all(|c| c.is_ascii_digit()))
    };
    if ipv4_style {
        if host.parse::<std::net::Ipv4Addr>().is_err() {
            return Err(InvalidUrl(format!("Invalid IPv4 address: {}", crate::pyobj::py_repr_str(host))));
        }
        return Ok(host.to_owned());
    }
    if host.len() >= 2 && host.starts_with('[') && host.ends_with(']') {
        let inner = &host[1..host.len() - 1];
        if inner.parse::<std::net::Ipv6Addr>().is_err() {
            return Err(InvalidUrl(format!("Invalid IPv6 address: {}", crate::pyobj::py_repr_str(host))));
        }
        return Ok(inner.to_owned());
    }
    if host.is_ascii() {
        let safe = format!("{SUB_DELIMS}\"`{{}}%|\\");
        return Ok(quote(&host.to_ascii_lowercase(), &safe));
    }
    Err(InvalidUrl(format!("Invalid IDNA hostname: {}", crate::pyobj::py_repr_str(host))))
}

fn normalize_port(port: Option<&str>, scheme: &str) -> Result<Option<u32>, InvalidUrl> {
    let Some(port) = port.filter(|port| !port.is_empty()) else { return Ok(None) };
    let value = match crate::pyobj::python_int_text(port) {
        crate::pyobj::IntText::Value(value) => value,
        _ => return Err(InvalidUrl(format!("Invalid port: {}", crate::pyobj::py_repr_str(port)))),
    };
    let default = match scheme {
        "ftp" => Some(21),
        "http" | "ws" => Some(80),
        "https" | "wss" => Some(443),
        _ => None,
    };
    if Some(value) == default {
        return Ok(None);
    }
    u32::try_from(value)
        .map(Some)
        .map_err(|_| InvalidUrl(format!("Invalid port: {}", crate::pyobj::py_repr_str(port))))
}

fn normalize_path(path: &str) -> String {
    if !path.contains('.') {
        return path.to_owned();
    }
    let components: Vec<&str> = path.split('/').collect();
    if !components.iter().any(|c| *c == "." || *c == "..") {
        return path.to_owned();
    }
    let mut output: Vec<&str> = Vec::new();
    for component in components {
        match component {
            "." => {}
            ".." => {
                if !output.is_empty() && output != [""] {
                    output.pop();
                }
            }
            other => output.push(other),
        }
    }
    output.join("/")
}

impl HttpxUrl {
    /// `httpx.URL(url)`.
    pub fn parse(url: &str) -> Result<Self, InvalidUrl> {
        if url.chars().count() > MAX_URL_LENGTH {
            return Err(InvalidUrl("URL too long".into()));
        }
        check_printable(url, None)?;
        let (scheme, authority, path, query, fragment) = split_reference(url);
        Self::from_parts(&scheme, &authority, &path, query, fragment)
    }

    fn from_parts(
        scheme: &str,
        authority: &str,
        path: &str,
        query: Option<String>,
        fragment: Option<String>,
    ) -> Result<Self, InvalidUrl> {
        let (userinfo, host, port) = split_authority(authority);
        let scheme_lower = scheme.to_ascii_lowercase();
        let userinfo = quote(&userinfo, &userinfo_safe());
        let host = encode_host(&host)?;
        // normalize_port compares with the scheme as written.
        let port = normalize_port(port.as_deref(), scheme)?;
        let has_scheme = !scheme_lower.is_empty();
        let has_authority = !userinfo.is_empty() || !host.is_empty() || port.is_some();
        if has_authority && !path.is_empty() && !path.starts_with('/') {
            return Err(InvalidUrl("For absolute URLs, path must be empty or begin with '/'".into()));
        }
        if !has_scheme && !has_authority {
            if path.starts_with("//") {
                return Err(InvalidUrl("Relative URLs cannot have a path starting with '//'".into()));
            }
            if path.starts_with(':') {
                return Err(InvalidUrl("Relative URLs cannot have a path starting with ':'".into()));
            }
        }
        let path = if has_scheme || has_authority { normalize_path(path) } else { path.to_owned() };
        Ok(Self {
            scheme: scheme_lower,
            userinfo,
            host,
            port,
            path: quote(&path, &path_safe()),
            query: query.map(|query| quote(&query, &query_safe())),
            fragment: fragment.map(|fragment| quote(&fragment, &frag_safe())),
        })
    }

    /// `authority`: userinfo, host (bracketed when it holds `:`), port.
    pub fn authority(&self) -> String {
        let mut out = String::new();
        if !self.userinfo.is_empty() {
            out.push_str(&self.userinfo);
            out.push('@');
        }
        if self.host.contains(':') {
            out.push_str(&format!("[{}]", self.host));
        } else {
            out.push_str(&self.host);
        }
        if let Some(port) = self.port {
            out.push_str(&format!(":{port}"));
        }
        out
    }

    /// `raw_path`: the path (`/` when empty) and the query.
    pub fn raw_path(&self) -> String {
        let mut path = if self.path.is_empty() { "/".to_owned() } else { self.path.clone() };
        if let Some(query) = &self.query {
            path.push('?');
            path.push_str(query);
        }
        path
    }

    /// `copy_with(raw_path=...)`: the raw path splits at its first `?` into path and query.
    pub fn with_raw_path(&self, raw_path: &str) -> Result<Self, InvalidUrl> {
        check_printable(raw_path, Some("raw_path"))?;
        let (path, query) = match raw_path.split_once('?') {
            Some((path, query)) => (path.to_owned(), Some(query.to_owned())),
            None => (raw_path.to_owned(), None),
        };
        Self::from_parts(&self.scheme, &self.authority(), &path, query, self.fragment.clone())
    }

    /// The SDK's request URL for an endpoint path such as `/chat/completions`:
    /// `_enforce_trailing_slash` on the base, then `_prepare_url`.
    pub fn sdk_endpoint(base_url: &str, endpoint: &str) -> Result<Self, InvalidUrl> {
        let mut base = Self::parse(base_url)?;
        let raw = base.raw_path();
        if !raw.ends_with('/') {
            base = base.with_raw_path(&format!("{raw}/"))?;
        }
        let joined = format!("{}{}", base.raw_path(), endpoint.trim_start_matches('/'));
        base.with_raw_path(&joined)
    }
}

impl std::fmt::Display for HttpxUrl {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if !self.scheme.is_empty() {
            write!(f, "{}:", self.scheme)?;
        }
        let authority = self.authority();
        if !authority.is_empty() {
            write!(f, "//{authority}")?;
        }
        f.write_str(&self.path)?;
        if let Some(query) = &self.query {
            write!(f, "?{query}")?;
        }
        if let Some(fragment) = &self.fragment {
            write!(f, "#{fragment}")?;
        }
        Ok(())
    }
}
