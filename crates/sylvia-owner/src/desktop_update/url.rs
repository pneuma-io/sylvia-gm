//! The parts of CPython 3.12's `urllib.parse` and `ipaddress` the updater's location checks
//! call: `urlsplit` with its `ValueError` cases, and `unquote(..., errors="strict")`.
//!
//! The updater reports a `ValueError` from either as "The update location is invalid." and
//! every other refusal as "...does not use an official release location.", so which inputs
//! raise is behaviour. The reference is the Windows build's CPython 3.12.11 (the only target
//! the updater supports); `update_vectors` compares both functions with it.

use unicode_normalization::UnicodeNormalization;

/// `SplitResult`: the five parts, with `scheme` lowercased as Python does.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Split {
    pub scheme: String,
    pub netloc: String,
    pub path: String,
    pub query: String,
    pub fragment: String,
}

/// `urlsplit`'s `ValueError`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidUrl;

const SCHEME_CHARS: &str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.";

/// `urlsplit(url)` (no default scheme, fragments allowed).
pub fn urlsplit(url: &str) -> Result<Split, InvalidUrl> {
    // `url.lstrip(_WHATWG_C0_CONTROL_OR_SPACE)`, then the unsafe bytes removed everywhere.
    let url: String = url
        .trim_start_matches(|c: char| c <= ' ')
        .chars()
        .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
        .collect();
    let mut scheme = String::new();
    let mut rest: &str = &url;
    if let Some(index) = url.find(':') {
        let first = url.chars().next();
        if index > 0
            && first.is_some_and(|c| c.is_ascii() && c.is_ascii_alphabetic())
            && url[..index].chars().all(|c| SCHEME_CHARS.contains(c))
        {
            scheme = url[..index].to_ascii_lowercase();
            rest = &url[index + 1..];
        }
    }
    let mut netloc = String::new();
    let mut remainder = rest.to_string();
    if let Some(body) = rest.strip_prefix("//") {
        let delimiter = body.find(['/', '?', '#']).unwrap_or(body.len());
        netloc = body[..delimiter].to_string();
        remainder = body[delimiter..].to_string();
        let open = netloc.contains('[');
        let close = netloc.contains(']');
        if open != close {
            return Err(InvalidUrl);
        }
        if open && close {
            check_bracketed_netloc(&netloc)?;
        }
    }
    let mut fragment = String::new();
    if let Some((before, after)) = remainder.split_once('#') {
        fragment = after.to_string();
        remainder = before.to_string();
    }
    let mut query = String::new();
    if let Some((before, after)) = remainder.split_once('?') {
        query = after.to_string();
        remainder = before.to_string();
    }
    check_netloc(&netloc)?;
    Ok(Split { scheme, netloc, path: remainder, query, fragment })
}

/// `_check_bracketed_netloc(netloc)`.
fn check_bracketed_netloc(netloc: &str) -> Result<(), InvalidUrl> {
    let host_and_port = netloc.rsplit_once('@').map_or(netloc, |(_, after)| after);
    let hostname = match host_and_port.split_once('[') {
        Some((before, bracketed)) => {
            if !before.is_empty() {
                return Err(InvalidUrl);
            }
            let (hostname, port) = bracketed.split_once(']').unwrap_or((bracketed, ""));
            if !port.is_empty() && !port.starts_with(':') {
                return Err(InvalidUrl);
            }
            hostname
        }
        None => host_and_port.split_once(':').map_or(host_and_port, |(host, _)| host),
    };
    check_bracketed_host(hostname)
}

/// `_check_bracketed_host(hostname)`.
fn check_bracketed_host(hostname: &str) -> Result<(), InvalidUrl> {
    if let Some(rest) = hostname.strip_prefix('v') {
        // `re.match(r"\Av[a-fA-F0-9]+\..+\Z", hostname)`; `.` matches anything but `\n`.
        let digits = rest.chars().take_while(char::is_ascii_hexdigit).count();
        let after = &rest[digits..];
        let valid = digits > 0 && after.starts_with('.') && after.len() > 1 && !after[1..].contains('\n');
        return if valid { Ok(()) } else { Err(InvalidUrl) };
    }
    // `ipaddress.ip_address(hostname)` must succeed and must not be IPv4.
    if is_ipv4(hostname) || !is_ipv6(hostname) {
        return Err(InvalidUrl);
    }
    Ok(())
}

/// `IPv4Address(text)` succeeds.
fn is_ipv4(text: &str) -> bool {
    ipv4_value(text).is_some()
}

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

/// `IPv6Address(text)` succeeds: CPython 3.12's `_split_scope_id` and `_ip_int_from_string`.
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
    let hextet = |text: &str| -> bool {
        // `_parse_hextet`: hex digits only, at most four, and `int(text, 16)` (which refuses "").
        !text.is_empty() && text.len() <= 4 && text.bytes().all(|byte| byte.is_ascii_hexdigit())
    };
    let low_parts = &parts[parts.len() - low..];
    if parts[..high].iter().all(|part| hextet(part)) && low_parts.iter().all(|part| hextet(part)) {
        Some(())
    } else {
        None
    }
}

/// `_checknetloc(netloc)`: a non-ASCII location whose NFKC form introduces a delimiter.
fn check_netloc(netloc: &str) -> Result<(), InvalidUrl> {
    if netloc.is_empty() || netloc.is_ascii() {
        return Ok(());
    }
    let reduced: String = netloc.chars().filter(|c| !matches!(c, '@' | ':' | '#' | '?')).collect();
    let normalized: String = reduced.nfkc().collect();
    if reduced == normalized {
        return Ok(());
    }
    if normalized.contains(['/', '?', '#', '@', ':']) {
        return Err(InvalidUrl);
    }
    Ok(())
}

/// `unquote(text, errors="strict")`: `%XX` decoded in each run of ASCII characters and the
/// bytes read as strict UTF-8; a `%` not followed by two hex digits is kept. Fails where
/// Python raises `UnicodeDecodeError`.
pub fn unquote_strict(text: &str) -> Result<String, InvalidUrl> {
    if !text.contains('%') {
        return Ok(text.to_string());
    }
    let mut result = String::new();
    let mut run = String::new();
    let flush = |run: &mut String, result: &mut String| -> Result<(), InvalidUrl> {
        if !run.is_empty() {
            let bytes = unquote_to_bytes(run.as_bytes());
            result.push_str(std::str::from_utf8(&bytes).map_err(|_| InvalidUrl)?);
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

/// `_unquote_impl(bytes)`.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_like_python() {
        let split = urlsplit("HTTPS://github.com/a/b?x#y").unwrap();
        assert_eq!(split.scheme, "https");
        assert_eq!(split.netloc, "github.com");
        assert_eq!(split.path, "/a/b");
        assert_eq!(split.query, "x");
        assert_eq!(split.fragment, "y");
        assert_eq!(urlsplit("https://[::1/").unwrap_err(), InvalidUrl);
        assert!(urlsplit("https://[::1]/").is_ok());
        assert!(urlsplit("https://[127.0.0.1]/").is_err());
        assert!(urlsplit("https://[v1.x]/").is_ok());
    }

    #[test]
    fn unquotes_like_python() {
        assert_eq!(unquote_strict("a%2Fb%zz%").unwrap(), "a/b%zz%");
        assert!(unquote_strict("%ff").is_err());
        assert_eq!(unquote_strict("%C3%A9é").unwrap(), "éé");
        assert!(has_bare_percent("a%2"));
        assert!(!has_bare_percent("a%2f"));
    }
}
