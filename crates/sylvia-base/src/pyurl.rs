//! `urllib.parse` as CPython 3.12.11 does it: `urlsplit` with every `ValueError` it raises,
//! `urlunsplit`, the `hostname`, `port`, `username` and `password` properties of its result,
//! and `unquote(..., errors="strict")`.
//!
//! The one shared port: provider routing compares hostnames and rebuilds URLs, and the desktop
//! updater checks release locations, so which inputs raise and with which message is
//! behaviour. `urlsplit` follows `Lib/urllib/parse.py` step by step:
//!
//! - leading C0 controls and spaces are stripped, and tab, CR and LF removed everywhere;
//! - the scheme is taken and lowercased only when it is a valid scheme;
//! - unbalanced IPv6 brackets are refused, and a bracketed host must be an IPvFuture address or
//!   an IPv6 address `ipaddress.ip_address` accepts (`_check_bracketed_netloc`,
//!   `_check_bracketed_host`, with `ipaddress`'s own parsing);
//! - a non-ASCII network location whose NFKC form introduces a delimiter is refused
//!   (`_checknetloc`; `unicode-normalization` 0.1.22 is Unicode 15.0, as CPython 3.12).
//!
//! The `ipaddress` checks and `_checknetloc` come from lane 5's updater port
//! (`desktop_update/url.rs`, PR 289), merged here with lane 4's provider port.

use crate::pystr::repr;
use unicode_normalization::UnicodeNormalization;

/// The five parts of `urlsplit(url)` (`SplitResult`).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SplitUrl {
    pub scheme: String,
    pub netloc: String,
    pub path: String,
    pub query: String,
    pub fragment: String,
}

/// A `ValueError` from `urlsplit`, a property, or `unquote`; the text is `str(error)`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct UrlError(pub String);

fn invalid(message: impl Into<String>) -> UrlError {
    UrlError(message.into())
}

const SCHEME_CHARS: &str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.";

/// `uses_netloc`.
const USES_NETLOC: [&str; 27] = [
    "",
    "ftp",
    "http",
    "gopher",
    "nntp",
    "telnet",
    "imap",
    "wais",
    "file",
    "mms",
    "https",
    "shttp",
    "snews",
    "prospero",
    "rtsp",
    "rtsps",
    "rtspu",
    "rsync",
    "svn",
    "svn+ssh",
    "sftp",
    "nfs",
    "git",
    "git+ssh",
    "ws",
    "wss",
    "itms-services",
];

/// `urlsplit(url)` (no default scheme, fragments allowed).
pub fn urlsplit(url: &str) -> Result<SplitUrl, UrlError> {
    // `url.lstrip(_WHATWG_C0_CONTROL_OR_SPACE)`, then `_UNSAFE_URL_BYTES_TO_REMOVE` everywhere.
    let mut url: String = url
        .trim_start_matches(|c: char| c <= ' ')
        .chars()
        .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
        .collect();
    let mut result = SplitUrl::default();
    if let Some(colon) = url.find(':') {
        let first = url.chars().next();
        if colon > 0
            && first.is_some_and(|c| c.is_ascii_alphabetic())
            && url[..colon].chars().all(|c| SCHEME_CHARS.contains(c))
        {
            result.scheme = url[..colon].to_ascii_lowercase();
            url = url[colon + 1..].to_owned();
        }
    }
    if let Some(rest) = url.strip_prefix("//") {
        // `_splitnetloc(url, 2)`.
        let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
        result.netloc = rest[..end].to_owned();
        let remainder = rest[end..].to_owned();
        let open = result.netloc.contains('[');
        let close = result.netloc.contains(']');
        if open != close {
            return Err(invalid("Invalid IPv6 URL"));
        }
        if open && close {
            check_bracketed_netloc(&result.netloc)?;
        }
        url = remainder;
    }
    if let Some((before, fragment)) = url.split_once('#') {
        result.fragment = fragment.to_owned();
        url = before.to_owned();
    }
    if let Some((before, query)) = url.split_once('?') {
        result.query = query.to_owned();
        url = before.to_owned();
    }
    check_netloc(&result.netloc)?;
    result.path = url;
    Ok(result)
}

/// `_check_bracketed_netloc(netloc)`: nothing before the bracket, nothing but a port after it.
fn check_bracketed_netloc(netloc: &str) -> Result<(), UrlError> {
    let host_and_port = netloc.rsplit_once('@').map_or(netloc, |(_, host)| host);
    let hostname = match host_and_port.split_once('[') {
        Some((before, bracketed)) => {
            if !before.is_empty() {
                return Err(invalid("Invalid IPv6 URL"));
            }
            let (hostname, port) = bracketed.split_once(']').unwrap_or((bracketed, ""));
            if !port.is_empty() && !port.starts_with(':') {
                return Err(invalid("Invalid IPv6 URL"));
            }
            hostname
        }
        None => host_and_port.split_once(':').map_or(host_and_port, |(host, _)| host),
    };
    check_bracketed_host(hostname)
}

/// `_check_bracketed_host(hostname)`: `v<hex>.<anything>`, or an address `ip_address` accepts
/// that is not IPv4.
fn check_bracketed_host(hostname: &str) -> Result<(), UrlError> {
    if let Some(rest) = hostname.strip_prefix('v') {
        // `re.match(r"\Av[a-fA-F0-9]+\..+\Z", hostname)`; `.` matches anything but `\n`.
        let digits = rest.chars().take_while(char::is_ascii_hexdigit).count();
        let after = &rest[digits..];
        let valid = digits > 0 && after.starts_with('.') && after.len() > 1 && !after[1..].contains('\n');
        return if valid { Ok(()) } else { Err(invalid("IPvFuture address is invalid")) };
    }
    if ipv4_value(hostname).is_some() {
        return Err(invalid("An IPv4 address cannot be in brackets"));
    }
    if !is_ipv6(hostname) {
        return Err(invalid(format!("{} does not appear to be an IPv4 or IPv6 address", repr(hostname))));
    }
    Ok(())
}

/// `IPv4Address(text)`'s value, `None` where it raises.
fn ipv4_value(text: &str) -> Option<u32> {
    if text.is_empty() || text.contains('/') {
        return None;
    }
    let octets: Vec<&str> = text.split('.').collect();
    if octets.len() != 4 {
        return None;
    }
    let mut value = 0u32;
    for octet in octets {
        // `_parse_octet`: ASCII decimal digits, at most three, no leading zero, at most 255.
        if octet.is_empty() || !octet.bytes().all(|byte| byte.is_ascii_digit()) || octet.len() > 3 {
            return None;
        }
        if octet != "0" && octet.starts_with('0') {
            return None;
        }
        let number: u32 = octet.parse().ok()?;
        if number > 255 {
            return None;
        }
        value = (value << 8) | number;
    }
    Some(value)
}

/// `IPv6Address(text)` succeeds: `_split_scope_id` and `_ip_int_from_string`.
fn is_ipv6(text: &str) -> bool {
    if text.contains('/') {
        return false;
    }
    let address = match text.split_once('%') {
        None => text,
        Some((address, scope)) => {
            if scope.is_empty() || scope.contains('%') {
                return false;
            }
            address
        }
    };
    ipv6_parts(address).is_some()
}

const HEXTET_COUNT: usize = 8;

fn ipv6_parts(text: &str) -> Option<()> {
    if text.is_empty() || text.chars().count() > 45 {
        return None;
    }
    let max_parts = HEXTET_COUNT + 1;
    // `split(':', maxsplit=9)`: at most ten parts, the last keeping any further colons.
    let mut parts: Vec<String> = text.splitn(max_parts + 1, ':').map(str::to_string).collect();
    if parts.len() < 3 {
        return None;
    }
    if parts.last().is_some_and(|last| last.contains('.')) {
        let value = ipv4_value(&parts.pop()?)?;
        parts.push(format!("{:x}", (value >> 16) & 0xffff));
        parts.push(format!("{:x}", value & 0xffff));
    }
    if parts.len() > max_parts {
        return None;
    }
    let mut skip_index = None;
    for (index, part) in parts.iter().enumerate().take(parts.len() - 1).skip(1) {
        if part.is_empty() {
            if skip_index.is_some() {
                return None;
            }
            skip_index = Some(index);
        }
    }
    let (high, low) = if let Some(skip) = skip_index {
        let mut high = skip;
        let mut low = parts.len() - skip - 1;
        if parts[0].is_empty() {
            high -= 1;
            if high != 0 {
                return None;
            }
        }
        if parts[parts.len() - 1].is_empty() {
            low -= 1;
            if low != 0 {
                return None;
            }
        }
        if HEXTET_COUNT < high + low + 1 {
            return None;
        }
        (high, low)
    } else {
        if parts.len() != HEXTET_COUNT || parts[0].is_empty() || parts[parts.len() - 1].is_empty() {
            return None;
        }
        (parts.len(), 0)
    };
    // `_parse_hextet`: hex digits only, at most four, and `int(text, 16)` (which refuses "").
    let hextet =
        |text: &str| !text.is_empty() && text.len() <= 4 && text.bytes().all(|byte| byte.is_ascii_hexdigit());
    let low_parts = &parts[parts.len() - low..];
    (parts[..high].iter().all(|part| hextet(part)) && low_parts.iter().all(|part| hextet(part))).then_some(())
}

/// `_checknetloc(netloc)`: a non-ASCII location whose NFKC form introduces a delimiter.
fn check_netloc(netloc: &str) -> Result<(), UrlError> {
    if netloc.is_empty() || netloc.is_ascii() {
        return Ok(());
    }
    let reduced: String = netloc.chars().filter(|c| !matches!(c, '@' | ':' | '#' | '?')).collect();
    let normalized: String = reduced.nfkc().collect();
    if reduced == normalized {
        return Ok(());
    }
    if normalized.contains(['/', '?', '#', '@', ':']) {
        return Err(invalid(format!(
            "netloc '{netloc}' contains invalid characters under NFKC normalization"
        )));
    }
    Ok(())
}

impl SplitUrl {
    /// `_hostinfo`: the host and the port text after any user information.
    fn hostinfo(&self) -> (&str, Option<&str>) {
        let hostinfo = self.netloc.rsplit_once('@').map_or(self.netloc.as_str(), |(_, host)| host);
        let (hostname, port) = match hostinfo.split_once('[') {
            Some((_, bracketed)) => {
                let (hostname, after) = bracketed.split_once(']').unwrap_or((bracketed, ""));
                (hostname, after.split_once(':').map_or("", |(_, port)| port))
            }
            None => hostinfo.split_once(':').unwrap_or((hostinfo, "")),
        };
        (hostname, (!port.is_empty()).then_some(port))
    }

    /// `_userinfo`: the user name and password before the last `@`.
    fn userinfo(&self) -> (Option<&str>, Option<&str>) {
        match self.netloc.rsplit_once('@') {
            Some((userinfo, _)) => match userinfo.split_once(':') {
                Some((username, password)) => (Some(username), Some(password)),
                None => (Some(userinfo), None),
            },
            None => (None, None),
        }
    }

    /// `.username`.
    pub fn username(&self) -> Option<&str> {
        self.userinfo().0
    }

    /// `.password`.
    pub fn password(&self) -> Option<&str> {
        self.userinfo().1
    }

    /// `.hostname`: lowercased, without brackets, port or user information; `None` when empty.
    /// A zone identifier after `%` keeps its case.
    pub fn hostname(&self) -> Option<String> {
        let (hostname, _) = self.hostinfo();
        if hostname.is_empty() {
            return None;
        }
        Some(match hostname.split_once('%') {
            Some((host, zone)) => format!("{}%{zone}", host.to_lowercase()),
            None => hostname.to_lowercase(),
        })
    }

    /// `.port`: `Ok(None)` without one, `Err` where Python raises `ValueError`.
    pub fn port(&self) -> Result<Option<u16>, UrlError> {
        let Some(port) = self.hostinfo().1 else { return Ok(None) };
        // `port.isdigit() and port.isascii()`: ASCII digits only.
        if !port.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(invalid(format!("Port could not be cast to integer value as {}", repr(port))));
        }
        // `int(port)`: leading zeros are fine; more than 4,300 digits is `int()`'s own refusal.
        let value = match crate::pystr::parse_int(port) {
            Ok(value) => value,
            Err(crate::pystr::IntParseError::OutOfRange { .. }) => {
                return Err(invalid("Port out of range 0-65535"))
            }
            Err(crate::pystr::IntParseError::Invalid(error)) => return Err(invalid(error.message)),
        };
        u16::try_from(value).map(Some).map_err(|_| invalid("Port out of range 0-65535"))
    }
}

/// `urlunsplit((scheme, netloc, path, query, fragment))`.
pub fn urlunsplit(parts: &SplitUrl) -> String {
    let mut url = parts.path.clone();
    if !parts.netloc.is_empty() {
        if !url.is_empty() && !url.starts_with('/') {
            url.insert(0, '/');
        }
        url = format!("//{}{url}", parts.netloc);
    } else if url.starts_with("//")
        || (!parts.scheme.is_empty()
            && USES_NETLOC.contains(&parts.scheme.as_str())
            && (url.is_empty() || url.starts_with('/')))
    {
        url = format!("//{url}");
    }
    if !parts.scheme.is_empty() {
        url = format!("{}:{url}", parts.scheme);
    }
    if !parts.query.is_empty() {
        url = format!("{url}?{}", parts.query);
    }
    if !parts.fragment.is_empty() {
        url = format!("{url}#{}", parts.fragment);
    }
    url
}

/// `urlsplit(url).hostname`, or `None` where `urlsplit` raises: a URL that does not split
/// matches no known host.
pub fn hostname(url: &str) -> Option<String> {
    urlsplit(url).ok()?.hostname()
}

/// `unquote(text, errors="strict")`: `%XX` decoded in each run of ASCII characters, the bytes
/// read as strict UTF-8; a `%` not followed by two hex digits is kept. `Err` where Python
/// raises `UnicodeDecodeError` (a `ValueError`).
pub fn unquote_strict(text: &str) -> Result<String, UrlError> {
    if !text.contains('%') {
        return Ok(text.to_string());
    }
    let mut result = String::new();
    let mut run = String::new();
    let flush = |run: &mut String, result: &mut String| -> Result<(), UrlError> {
        if !run.is_empty() {
            let bytes = unquote_to_bytes(run.as_bytes());
            let decoded = String::from_utf8(bytes.clone())
                .map_err(|error| invalid(crate::pyyaml::utf8_error_text(&bytes, &error)))?;
            result.push_str(&decoded);
            run.clear();
        }
        Ok(())
    };
    for character in text.chars() {
        if character.is_ascii() {
            run.push(character);
        } else {
            flush(&mut run, &mut result)?;
            result.push(character);
        }
    }
    flush(&mut run, &mut result)?;
    Ok(result)
}

/// `_unquote_impl` / `unquote_to_bytes(bytes)`.
fn unquote_to_bytes(raw: &[u8]) -> Vec<u8> {
    let mut bits = raw.split(|byte| *byte == b'%');
    let mut result = bits.next().unwrap_or_default().to_vec();
    for item in bits {
        let decoded = (item.len() >= 2)
            .then(|| std::str::from_utf8(&item[..2]).ok())
            .flatten()
            .filter(|pair| pair.bytes().all(|byte| byte.is_ascii_hexdigit()))
            .and_then(|pair| u8::from_str_radix(pair, 16).ok());
        match decoded {
            Some(byte) => {
                result.push(byte);
                result.extend_from_slice(&item[2..]);
            }
            None => {
                result.push(b'%');
                result.extend_from_slice(item);
            }
        }
    }
    result
}

/// `re.search(r"%(?![0-9A-Fa-f]{2})", text)`: a `%` not followed by two hex digits.
pub fn has_bare_percent(text: &str) -> bool {
    let bytes = text.as_bytes();
    bytes.iter().enumerate().any(|(index, byte)| {
        *byte == b'%'
            && !(bytes.get(index + 1).is_some_and(u8::is_ascii_hexdigit)
                && bytes.get(index + 2).is_some_and(u8::is_ascii_hexdigit))
    })
}
