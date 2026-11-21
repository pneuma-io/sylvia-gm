//! `urllib.parse.urlsplit` and `urlunsplit` as Python 3.12 does them, with the `hostname` and
//! `port` properties of the result.
//!
//! Provider routing compares hostnames (`openrouter.ai`, `api.groq.com`, ...) and the OpenCode
//! route rewrite rebuilds URLs, so the split must agree with Python on odd input too: leading
//! control characters and spaces are dropped, tabs and newlines anywhere are removed, the scheme
//! is lowercased only when it is a valid scheme, and unbalanced IPv6 brackets are an error.
//! Python's extra check on non-ASCII network locations (NFKC normalisation) is not ported; a
//! non-ASCII host is used as given.

/// The five parts of `urlsplit(url)`.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SplitUrl {
    pub scheme: String,
    pub netloc: String,
    pub path: String,
    pub query: String,
    pub fragment: String,
}

/// `ValueError("Invalid IPv6 URL")` and the other ways `urlsplit` refuses input.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UrlError(pub &'static str);

const SCHEME_CHARS: &str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.";
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

/// `urlsplit(url)`.
pub fn urlsplit(url: &str) -> Result<SplitUrl, UrlError> {
    let url = url.trim_start_matches(|c: char| c <= ' ');
    let mut url: String = url.chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
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
    if url.starts_with("//") {
        let rest = &url[2..];
        let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
        result.netloc = rest[..end].to_owned();
        url = rest[end..].to_owned();
        let open = result.netloc.contains('[');
        let close = result.netloc.contains(']');
        if open != close {
            return Err(UrlError("Invalid IPv6 URL"));
        }
        if open && close {
            check_bracketed_netloc(&result.netloc)?;
        }
    }
    if let Some((before, fragment)) = url.split_once('#') {
        result.fragment = fragment.to_owned();
        url = before.to_owned();
    }
    if let Some((before, query)) = url.split_once('?') {
        result.query = query.to_owned();
        url = before.to_owned();
    }
    result.path = url;
    Ok(result)
}

/// `_check_bracketed_netloc`: nothing may come before the bracket or between it and the port,
/// and the host must be an IPv6 or IPvFuture address.
fn check_bracketed_netloc(netloc: &str) -> Result<(), UrlError> {
    let host_and_port = netloc.rsplit_once('@').map_or(netloc, |(_, host)| host);
    let hostname = match host_and_port.split_once('[') {
        Some((before, bracketed)) => {
            if !before.is_empty() {
                return Err(UrlError("Invalid IPv6 URL"));
            }
            let (hostname, port) = bracketed.split_once(']').unwrap_or((bracketed, ""));
            if !port.is_empty() && !port.starts_with(':') {
                return Err(UrlError("Invalid IPv6 URL"));
            }
            hostname
        }
        None => host_and_port.split_once(':').map_or(host_and_port, |(host, _)| host),
    };
    check_bracketed_host(hostname)
}

/// `_check_bracketed_host`: `v<hex>.<anything>`, or an address `ipaddress.ip_address` accepts
/// that is not IPv4.
fn check_bracketed_host(hostname: &str) -> Result<(), UrlError> {
    if let Some(future) = hostname.strip_prefix('v') {
        let valid = future.split_once('.').is_some_and(|(version, rest)| {
            !version.is_empty() && version.chars().all(|c| c.is_ascii_hexdigit()) && !rest.is_empty()
        });
        return if valid { Ok(()) } else { Err(UrlError("IPvFuture address is invalid")) };
    }
    if hostname.parse::<std::net::Ipv4Addr>().is_ok() {
        return Err(UrlError("An IPv4 address cannot be in brackets"));
    }
    let address = match hostname.split_once('%') {
        Some((address, scope)) if !scope.is_empty() && !scope.contains('%') => address,
        Some(_) => return Err(UrlError("does not appear to be an IPv4 or IPv6 address")),
        None => hostname,
    };
    if address.parse::<std::net::Ipv6Addr>().is_err() {
        return Err(UrlError("does not appear to be an IPv4 or IPv6 address"));
    }
    Ok(())
}

impl SplitUrl {
    fn hostinfo(&self) -> (&str, &str) {
        let hostinfo = self.netloc.rsplit_once('@').map_or(self.netloc.as_str(), |(_, host)| host);
        match hostinfo.split_once('[') {
            Some((_, bracketed)) => {
                let (hostname, after) = bracketed.split_once(']').unwrap_or((bracketed, ""));
                (hostname, after.split_once(':').map_or("", |(_, port)| port))
            }
            None => hostinfo.split_once(':').unwrap_or((hostinfo, "")),
        }
    }

    /// `.hostname`: lowercased, without brackets, port or user information; `None` when empty.
    /// A zone identifier after `%` keeps its case, as in Python.
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
        let (_, port) = self.hostinfo();
        if port.is_empty() {
            return Ok(None);
        }
        if !port.is_ascii() || !port.chars().all(|c| c.is_ascii_digit()) {
            return Err(UrlError("Port could not be cast to integer value"));
        }
        let value: u32 = port.parse().map_err(|_| UrlError("Port out of range 0-65535"))?;
        u16::try_from(value).map(Some).map_err(|_| UrlError("Port out of range 0-65535"))
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

/// `urlsplit(url).hostname`, or `None` where Python's `urlsplit` would raise. Every caller in
/// the provider code reads the hostname of a configured URL this way; a URL that does not split
/// matches no known host.
pub fn hostname(url: &str) -> Option<String> {
    urlsplit(url).ok()?.hostname()
}
